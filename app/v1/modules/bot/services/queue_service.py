import logging
import asyncio
import gc
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from beanie.operators import Or
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS
from app.v1.core.settings import (
    MAIN_SERVER_API_BASE_URL,
    MAIN_SERVER_API_TOKEN,
    MACHINE_NAME,
    PRINTSMITH_COMPANY,
    PRINTSMITH_PASSWORD,
    PRINTSMITH_URL,
    PRINTSMITH_USERNAME,
    QUEUE_BUSY_POLL_INTERVAL_SECONDS,
    QUEUE_ENFORCE_MACHINE_ASSIGNMENT,
    QUEUE_IDLE_POLL_INTERVAL_SECONDS,
)
from app.v1.modules.bot.services.estimate_service import run_estimate_flow
from app.v1.schemas.jobqueuemodel import JobQueueDocument, JobQueueStatus

logger = logging.getLogger(__name__)
_poller_lock = asyncio.Lock()
_active_poll_task: asyncio.Task | None = None


def _flush_log_handlers() -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _cleanup_after_job() -> None:
    from app.v1.modules.bot import csv_logger

    _flush_log_handlers()
    csv_logger.shutdown()
    gc.collect()


def _main_server_endpoint(path: str) -> str:
    base = MAIN_SERVER_API_BASE_URL.rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    if base.endswith("/api/v1"):
        return f"{base}{clean_path}"
    return f"{base}/api/v1{clean_path}"


def _auth_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if MAIN_SERVER_API_TOKEN:
        headers["Authorization"] = f"Bearer {MAIN_SERVER_API_TOKEN}"
    return headers


def _build_runtime_credentials() -> Dict[str, str]:
    company = PRINTSMITH_COMPANY
    if not company and PRINTSMITH_URL:
        hostname = urlparse(PRINTSMITH_URL).hostname or ""
        company = hostname.split(".", 1)[0].strip()

    return {
        "printsmith_url": PRINTSMITH_URL,
        "username": PRINTSMITH_USERNAME,
        "password": PRINTSMITH_PASSWORD,
        "company": company,
    }


def _is_job_assigned_to_current_machine(machine_name: Optional[str]) -> bool:
    if not QUEUE_ENFORCE_MACHINE_ASSIGNMENT:
        return True
    assigned_machine = str(machine_name or "").strip()
    current_machine = str(MACHINE_NAME or "").strip()
    if not assigned_machine or not current_machine:
        return True
    return assigned_machine.lower() == current_machine.lower()


def _normalize_runtime_credentials(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    payload = data or {}
    printsmith_url = str(
        payload.get("printsmith_url") or payload.get("url") or PRINTSMITH_URL
    ).strip()
    username = str(
        payload.get("printsmith_username")
        or payload.get("username")
        or PRINTSMITH_USERNAME
    ).strip()
    password = str(
        payload.get("printsmith_password")
        or payload.get("password")
        or PRINTSMITH_PASSWORD
    ).strip()
    company = str(
        payload.get("printsmith_company")
        or payload.get("company")
        or PRINTSMITH_COMPANY
    ).strip()

    if not company and printsmith_url:
        hostname = urlparse(printsmith_url).hostname or ""
        company = hostname.split(".", 1)[0].strip()

    return {
        "printsmith_url": printsmith_url,
        "username": username,
        "password": password,
        "company": company,
    }


async def fetch_main_server_record(queue_id: str) -> Dict[str, Any]:
    if not MAIN_SERVER_API_BASE_URL:
        raise HTTPException(
            status_code=500, detail="MAIN_SERVER_API_BASE_URL is not configured"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            _main_server_endpoint(f"/quotation/job/{queue_id}/quote-detail"),
            headers=_auth_headers(),
        )
        response.raise_for_status()
        payload = response.json()

    if isinstance(payload, dict) and "data" in payload:
        return payload["data"] or {}
    return payload


def _build_bot_quote_record(
    payload: Dict[str, Any], job: JobQueueDocument
) -> Dict[str, Any]:
    quote = payload.get("quote") or {}
    requirements = payload.get("requirements") or quote.get("requirements") or {}

    quote_id = str(
        quote.get("_id")
        or quote.get("id")
        or quote.get("quote_id")
        or job.quotation_id
    )

    return {
        "_id": quote_id,
        "quote_id": quote_id,
        "tenant_id": quote.get("tenant_id"),
        "user_email": quote.get("user_email", ""),
        "account_name": quote.get("account_name", ""),
        "contact_person": quote.get("contact_person", ""),
        "contact_email": quote.get("contact_email", ""),
        "contact_phone": quote.get("contact_phone", ""),
        "description": quote.get("description", ""),
        "summary": quote.get("summary", ""),
        "requirements": {
            "stock_search": requirements.get("stock_search", ""),
            "quantity": requirements.get("quantity", ""),
            "job_charges": requirements.get("job_charges", []),
            "size": requirements.get("size", ""),
            "sides": requirements.get("sides", ""),
        },
    }


async def sync_job_with_main_server(job: JobQueueDocument) -> Dict[str, Any]:
    payload = await fetch_main_server_record(str(job.id))
    quote = payload.get("quote") or {}
    job_data = payload.get("job") or {}

    if quote:
        job.quotation_id = str(
            quote.get("_id")
            or quote.get("id")
            or quote.get("quote_id")
            or job.quotation_id
        )
        job.tenant_id = quote.get("tenant_id") or job.tenant_id

    if job_data:
        job.tenant_id = job_data.get("tenant_id") or job.tenant_id
        job.created_by = job_data.get("created_by") or job.created_by
        job.machine_name = job_data.get("machine_name") or job.machine_name

    job.updated_at = datetime.utcnow()
    await job.save()
    return payload


async def _notify_main_server(
    job: JobQueueDocument, result: Dict[str, Any]
) -> Dict[str, Any]:
    if not MAIN_SERVER_API_BASE_URL:
        return {
            "status": "skipped",
            "message": "MAIN_SERVER_API_BASE_URL is not configured",
        }

    payload = {
        "queue_id": str(job.id),
        "success": result.get("status") == "success",
        "summary_file_name": result.get("summary_file_name"),
        "summary_file_url": result.get("summary_file_url"),
        "error_message": (
            None if result.get("status") == "success" else result.get("message")
        ),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _main_server_endpoint("/quotation/job/result"),
            json=payload,
            headers=_auth_headers(),
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"status": "success", "http_status": response.status_code}


async def recover_incomplete_jobs() -> None:
    stuck_jobs = [
        job
        for job in await JobQueueDocument.find(
            JobQueueDocument.status == JobQueueStatus.processing
        ).to_list()
        if _is_job_assigned_to_current_machine(job.machine_name)
    ]
    if not stuck_jobs:
        logger.info(
            "Queue recovery check completed: no stuck jobs found for current machine=%s",
            MACHINE_NAME or "unset",
        )
        return

    logger.warning(
        "Queue recovery found %s stuck job(s) for current machine=%s",
        len(stuck_jobs),
        MACHINE_NAME or "unset",
    )
    for job in stuck_jobs:
        job.status = JobQueueStatus.pending
        job.last_error = "Recovered after service restart before previous run completed"
        job.failure_history.append(
            {
                "retry": job.retry_count,
                "message": job.last_error,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        job.updated_at = datetime.utcnow()
        await job.save()
        logger.info("Recovered stuck queue job queue_id=%s", getattr(job, "id", None))


async def process_job_queue_document(job: JobQueueDocument) -> Dict[str, Any]:
    started_at = datetime.utcnow()
    if job.status == JobQueueStatus.processing:
        return {
            "status": "skipped",
            "message": "Queue record is already processing",
        }
    if not _is_job_assigned_to_current_machine(job.machine_name):
        return {
            "status": "skipped",
            "message": (
                f"Queue record is assigned to machine '{job.machine_name}', "
                f"current machine is '{MACHINE_NAME or 'unset'}'"
            ),
        }

    job.status = JobQueueStatus.processing
    job.updated_at = started_at
    await job.save()
    logger.info(
        "Queue processing started queue_id=%s start_time=%s",
        getattr(job, "id", None),
        started_at.isoformat(),
    )

    try:
        logger.info("Queue step=fetch_main_record queue_id=%s", getattr(job, "id", None))
        payload = await fetch_main_server_record(str(job.id))
        quote = payload.get("quote") or {}
        job_data = payload.get("job") or {}
        psv_credentials = payload.get("psv_credentials") or {}
        quote_record = _build_bot_quote_record(payload, job)

        if quote:
            job.quotation_id = str(
                quote.get("_id")
                or quote.get("id")
                or quote.get("quote_id")
                or job.quotation_id
            )
            job.tenant_id = quote.get("tenant_id") or job.tenant_id

        if job_data:
            job.tenant_id = job_data.get("tenant_id") or job.tenant_id
            job.created_by = job_data.get("created_by") or job.created_by
            job.machine_name = job_data.get("machine_name") or job.machine_name

        if not _is_job_assigned_to_current_machine(job.machine_name):
            job.status = JobQueueStatus.pending
            job.updated_at = datetime.utcnow()
            await job.save()
            logger.info(
                "Queue job skipped queue_id=%s assigned_machine=%s current_machine=%s",
                getattr(job, "id", None),
                job.machine_name,
                MACHINE_NAME or "unset",
            )
            return {
                "status": "skipped",
                "message": (
                    f"Queue record is assigned to machine '{job.machine_name}', "
                    f"current machine is '{MACHINE_NAME or 'unset'}'"
                ),
            }

        job.updated_at = datetime.utcnow()
        await job.save()

        runtime_credentials = (
            _normalize_runtime_credentials(psv_credentials)
            or _build_runtime_credentials()
        )
        if not runtime_credentials["printsmith_url"]:
            raise HTTPException(
                status_code=500, detail="PRINTSMITH_URL is not configured"
            )
        if not runtime_credentials["username"]:
            raise HTTPException(
                status_code=500, detail="PRINTSMITH_USERNAME is not configured"
            )
        if not runtime_credentials["password"]:
            raise HTTPException(
                status_code=500, detail="PRINTSMITH_PASSWORD is not configured"
            )
        if not runtime_credentials["company"]:
            raise HTTPException(
                status_code=500, detail="PRINTSMITH_COMPANY is not configured"
            )

        logger.info(
            "Queue step=run_bot queue_id=%s flow_timeout_seconds=%s",
            getattr(job, "id", None),
            DEFAULT_TIMEOUT_SECONDS,
        )
        result = await run_in_threadpool(
            run_estimate_flow,
            runtime_credentials,
            quote_record,
        )

        ended_at = datetime.utcnow()
        total_time_used_seconds = round((ended_at - started_at).total_seconds(), 3)
        job.updated_at = ended_at

        if result.get("status") == "success":
            job.status = JobQueueStatus.complete
            job.file_name = result.get("summary_file_name")
            job.file_url = result.get("summary_file_url")
            job.last_error = None
        else:
            job.status = JobQueueStatus.failed
            job.retry_count += 1
            job.last_error = result.get("message") or "Bot processing failed"
            job.failure_history.append(
                {
                    "retry": job.retry_count,
                    "message": job.last_error,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

        await job.save()
        logger.info(
            "Queue processing finished queue_id=%s end_time=%s total_time_used_seconds=%s status=%s",
            getattr(job, "id", None),
            ended_at.isoformat(),
            total_time_used_seconds,
            job.status,
        )
        try:
            logger.info(
                "Queue step=notify_main_server queue_id=%s after bot upload and logout",
                getattr(job, "id", None),
            )
            result["main_server_callback"] = await _notify_main_server(job, result)
        except Exception as exc:
            logger.exception(
                "Failed to notify main server for queue_id=%s", getattr(job, "id", None)
            )
            result["main_server_callback"] = {
                "status": "error",
                "message": str(exc),
            }
        return result
    except Exception as exc:
        logger.exception(
            "Queue processing failed for queue_id=%s", getattr(job, "id", None)
        )
        job.status = JobQueueStatus.failed
        job.retry_count += 1
        job.last_error = str(exc)
        job.failure_history.append(
            {
                "retry": job.retry_count,
                "message": str(exc),
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        ended_at = datetime.utcnow()
        total_time_used_seconds = round((ended_at - started_at).total_seconds(), 3)
        job.updated_at = ended_at
        await job.save()
        logger.info(
            "Queue processing failed queue_id=%s end_time=%s total_time_used_seconds=%s error=%s",
            getattr(job, "id", None),
            ended_at.isoformat(),
            total_time_used_seconds,
            str(exc),
        )
        error_result = {
            "status": "error",
            "message": str(exc),
        }
        try:
            error_result["main_server_callback"] = await _notify_main_server(
                job, error_result
            )
        except Exception:
            logger.exception(
                "Failed to notify main server for queue_id=%s", getattr(job, "id", None)
            )
        return error_result
    finally:
        logger.info("Queue cleanup queue_id=%s", getattr(job, "id", None))
        _cleanup_after_job()


async def _run_pending_jobs_batch() -> None:
    logger.info("Queue scheduler checking pending jobs")
    processing_job = next(
        (
            job
            for job in await JobQueueDocument.find(
                JobQueueDocument.status == JobQueueStatus.processing
            ).to_list()
            if _is_job_assigned_to_current_machine(job.machine_name)
        ),
        None,
    )
    if processing_job is not None:
        logger.info(
            "Queue scheduler skipped because queue_id=%s is already processing on current_machine=%s",
            getattr(processing_job, "id", None),
            MACHINE_NAME or "unset",
        )
        return
    pending_jobs = await JobQueueDocument.find(
        Or(
            JobQueueDocument.status == JobQueueStatus.pending,
            JobQueueDocument.status == None,
        )
    ).to_list()
    logger.info("Queue scheduler found %s pending job(s)", len(pending_jobs))
    for job in pending_jobs:
        if not _is_job_assigned_to_current_machine(job.machine_name):
            logger.info(
                "Queue scheduler ignored queue_id=%s assigned_machine=%s current_machine=%s",
                getattr(job, "id", None),
                job.machine_name,
                MACHINE_NAME or "unset",
            )
            continue
        await process_job_queue_document(job)


async def poll_and_process_pending_jobs() -> Dict[str, Any]:
    if _poller_lock.locked():
        return {
            "status": "skipped",
            "message": "Queue poll is already running",
        }

    async with _poller_lock:
        await _run_pending_jobs_batch()
        return {
            "status": "success",
            "message": "Queue poll completed",
        }


async def get_queue_poll_sleep_seconds() -> int:
    processing_job = next(
        (
            job
            for job in await JobQueueDocument.find(
                JobQueueDocument.status == JobQueueStatus.processing
            ).to_list()
            if _is_job_assigned_to_current_machine(job.machine_name)
        ),
        None,
    )
    if processing_job is not None:
        return QUEUE_BUSY_POLL_INTERVAL_SECONDS
    return QUEUE_IDLE_POLL_INTERVAL_SECONDS


def schedule_queue_poll_if_idle() -> bool:
    global _active_poll_task

    if _active_poll_task is not None and not _active_poll_task.done():
        logger.info("Queue scheduler tick skipped because previous batch is still running")
        return False

    _active_poll_task = asyncio.create_task(poll_and_process_pending_jobs())
    return True
