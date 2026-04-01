import logging
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from beanie.operators import Or
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.v1.core.settings import (
    MAIN_SERVER_API_BASE_URL,
    MAIN_SERVER_API_TOKEN,
    PRINTSMITH_COMPANY,
    PRINTSMITH_PASSWORD,
    PRINTSMITH_URL,
    PRINTSMITH_USERNAME,
)
from app.v1.modules.bot.services.estimate_service import run_estimate_flow
from app.v1.schemas.jobqueuemodel import JobQueueDocument, JobQueueStatus

logger = logging.getLogger(__name__)
_poller_lock = asyncio.Lock()
_active_poll_task: asyncio.Task | None = None


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
        print(f'payload: {payload}')

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
        or job.record_id
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
        "requirements": {
            "stock_search": requirements.get("stock_search", ""),
            "quantity": requirements.get("quantity", ""),
            "job_charges": requirements.get("job_charges", []),
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
        job.record_id = str(
            quote.get("_id")
            or quote.get("id")
            or quote.get("quote_id")
            or job.record_id
        )
        job.tenant_id = quote.get("tenant_id") or job.tenant_id

    if job_data:
        job.tenant_id = job_data.get("tenant_id") or job.tenant_id
        job.created_by = job_data.get("created_by") or job.created_by

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
        "summary_file_url": result.get("summary_file_url")
        or result.get("summary_file_gcs_uri"),
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


async def process_job_queue_document(job: JobQueueDocument) -> Dict[str, Any]:
    now = datetime.utcnow()
    if job.is_processing:
        return {
            "status": "skipped",
            "message": "Queue record is already processing",
        }

    job.is_processing = True
    job.status = JobQueueStatus.processing
    job.updated_at = now
    await job.save()

    try:
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
            job.record_id = str(
                quote.get("_id")
                or quote.get("id")
                or quote.get("quote_id")
                or job.record_id
            )
            job.tenant_id = quote.get("tenant_id") or job.tenant_id

        if job_data:
            job.tenant_id = job_data.get("tenant_id") or job.tenant_id
            job.created_by = job_data.get("created_by") or job.created_by

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

        result = await run_in_threadpool(
            run_estimate_flow,
            runtime_credentials,
            quote_record,
        )

        job.is_processing = False
        job.updated_at = datetime.utcnow()

        if result.get("status") == "success":
            job.status = JobQueueStatus.complete
            job.file_name = result.get("summary_file_name")
            job.file_url = result.get("summary_file_url") or result.get(
                "summary_file_gcs_uri"
            )
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
        try:
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
        job.is_processing = False
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
        job.updated_at = datetime.utcnow()
        await job.save()
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


async def _run_pending_jobs_batch() -> None:
    print('checking pending taskes')
    pending_jobs = await JobQueueDocument.find(
        Or(
            JobQueueDocument.status == JobQueueStatus.pending,
            JobQueueDocument.status == None,
        ),
        JobQueueDocument.is_processing == False,
    ).to_list()

    for job in pending_jobs:
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


def schedule_queue_poll_if_idle() -> bool:
    global _active_poll_task

    if _active_poll_task is not None and not _active_poll_task.done():
        return False

    _active_poll_task = asyncio.create_task(poll_and_process_pending_jobs())
    return True
