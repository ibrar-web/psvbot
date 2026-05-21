import logging
import asyncio
import gc
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
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
    QUEUE_ENFORCE_MACHINE_ASSIGNMENT,
)
from app.v1.modules.bot.services.estimate_service import run_estimate_flow
from app.v1.schemas.jobqueuemodel import JobQueueDocument, JobQueueStatus

logger = logging.getLogger(__name__)
_task_execution_lock = asyncio.Lock()


def _cleanup_after_job() -> None:
    """Run after every job to release memory: flush logging, clean up bot logger, gc."""
    import logging as _logging

    # Flush all handlers on the bot logger to ensure no buffered records
    bot_logger = _logging.getLogger("app.v1.modules.bot")
    for handler in list(bot_logger.handlers):
        try:
            handler.flush()
        except Exception:
            pass

    # Flush root logger handlers too
    for handler in list(_logging.getLogger().handlers):
        try:
            handler.flush()
        except Exception:
            pass

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


def _validate_runtime_credentials(runtime_credentials: Dict[str, str]) -> None:
    if not runtime_credentials["printsmith_url"]:
        raise HTTPException(status_code=500, detail="PRINTSMITH_URL is not configured")
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
    payload: Dict[str, Any],
    job: Optional[JobQueueDocument] = None,
    queue_id: Optional[str] = None,
) -> Dict[str, Any]:
    quote = payload.get("quote") or {}
    raw_requirements = payload.get("requirements") or []
    tenant_credentials = payload.get("tenant_credentials") or {}
    fallback_quote_id = queue_id or ""
    if job is not None:
        fallback_quote_id = job.quotation_id or str(job.id)
    quote_id = str(
        quote.get("_id") or quote.get("id") or quote.get(
            "quote_id") or fallback_quote_id
    )

    if isinstance(raw_requirements, dict):
        raw_requirements = [raw_requirements]

    requirements = []
    for requirement in raw_requirements:
        if not isinstance(requirement, dict):
            continue
        requirements.append(
            {
                "stock_search": requirement.get("stock_search", ""),
                "quantity": requirement.get("quantity", ""),
                "size": requirement.get("size", ""),
                "sides": requirement.get("sides", ""),
                "description": requirement.get("description", ""),
                "job_method": requirement.get("job_method", ""),
                "job_charges": requirement.get("job_charges", []),
                "other_charges": requirement.get(
                    "other_charges",
                    requirement.get("other_chrages", []),
                ),
            }
        )

    record = {
        "_id": quote_id,
        "quote_id": quote_id,
        "estimate_id": payload.get("estimate_id") or None,
        "tenant_id": quote.get("tenant_id"),
        "user_email": quote.get("user_email", ""),
        "printsmith_url": tenant_credentials.get("printsmith_url") or "",
        "printsmith_username": tenant_credentials.get("printsmith_username") or "",
        "printsmith_password": tenant_credentials.get("printsmith_password") or "",
        "printsmith_company": tenant_credentials.get("printsmith_company") or "",
        "account_name": quote.get("account_name", ""),
        "contact_person": quote.get("contact_person", ""),
        "contact_email": quote.get("contact_email", ""),
        "contact_phone": quote.get("contact_phone", ""),
        "requirements": requirements,
    }

    logger.info("Normalized bot quote record: %s", record)
    print(f"[PSV][QueueService] Normalized bot quote record: {record}")
    return record


def _unwrap_task_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        merged = {**payload, **data}
        merged["callback_url"] = data.get("callback_url") or payload.get("callback_url")
        return merged

    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        merged = {**payload, **nested_payload}
        merged["callback_url"] = (
            nested_payload.get("callback_url") or payload.get("callback_url")
        )
        return merged

    return payload


def _extract_queue_id(payload: Dict[str, Any]) -> str:
    job_data = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    return str(
        payload.get("queue_id")
        or payload.get("job_queue_id")
        or payload.get("task_id")
        or payload.get("job_id")
        or job_data.get("_id")
        or job_data.get("id")
        or ""
    ).strip()


async def _get_job_queue_document(queue_id: str) -> Optional[JobQueueDocument]:
    if not queue_id:
        return None
    try:
        return await JobQueueDocument.get(queue_id)
    except Exception:
        logger.info("No local job_queue document found for queue_id=%s", queue_id)
        return None


async def _resolve_task_source_payload(
    task_payload: Dict[str, Any],
    queue_id: str,
) -> Dict[str, Any]:
    if any(
        key in task_payload
        for key in (
            "quote_record",
            "quote",
            "requirements",
            "tenant_credentials",
            "psv_credentials",
            "credentials",
        )
    ):
        return task_payload

    if queue_id:
        return await fetch_main_server_record(queue_id)

    raise HTTPException(
        status_code=400,
        detail="Cloud Task payload must include quote data or queue_id",
    )


def _build_quote_record_from_task_payload(
    payload: Dict[str, Any],
    job: Optional[JobQueueDocument],
    queue_id: str,
) -> Dict[str, Any]:
    quote_record = payload.get("quote_record")
    if isinstance(quote_record, dict):
        normalized_record = dict(quote_record)
        if "requirements" not in normalized_record and "requirements" in payload:
            normalized_record["requirements"] = payload.get("requirements")
        if "estimate_id" not in normalized_record and "estimate_id" in payload:
            normalized_record["estimate_id"] = payload.get("estimate_id")
        if "quote_id" not in normalized_record:
            normalized_record["quote_id"] = (
                normalized_record.get("_id") or queue_id or getattr(job, "quotation_id", "")
            )
        return normalized_record

    return _build_bot_quote_record(payload, job, queue_id)


def _extract_psv_credentials(
    payload: Dict[str, Any],
    quote_record: Dict[str, Any],
) -> Dict[str, Any]:
    return (
        payload.get("tenant_credentials")
        or payload.get("psv_credentials")
        or payload.get("credentials")
        or {
            "printsmith_url": quote_record.get("printsmith_url", ""),
            "printsmith_username": quote_record.get("printsmith_username", ""),
            "printsmith_password": quote_record.get("printsmith_password", ""),
            "printsmith_company": quote_record.get("printsmith_company", ""),
        }
    )


def _build_result_payload(
    *,
    queue_id: str,
    result: Dict[str, Any],
    started_at: datetime,
    ended_at: datetime,
    job: Optional[JobQueueDocument] = None,
) -> Dict[str, Any]:
    return {
        "queue_id": queue_id or (str(job.id) if job is not None else None),
        "quotation_id": getattr(job, "quotation_id", None),
        "success": result.get("status") == "success",
        "status": result.get("status"),
        "message": result.get("message"),
        "summary_file_name": result.get("summary_file_name"),
        "summary_file_url": result.get("summary_file_url"),
        "summary_file_storage_key": result.get("summary_file_storage_key"),
        "error_message": (
            None if result.get("status") == "success" else result.get("message")
        ),
        "estimate_totals": result.get("estimate_totals"),
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "total_time_used_seconds": round((ended_at - started_at).total_seconds(), 3),
    }


def _callback_headers(payload: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    authorization = str(
        payload.get("callback_authorization")
        or payload.get("callback_auth_header")
        or ""
    ).strip()
    callback_token = str(payload.get("callback_token") or "").strip()

    if authorization:
        headers["Authorization"] = authorization
    elif callback_token:
        headers["Authorization"] = f"Bearer {callback_token}"
    elif MAIN_SERVER_API_TOKEN:
        headers["Authorization"] = f"Bearer {MAIN_SERVER_API_TOKEN}"

    return headers


async def _post_callback_url(
    callback_url: str,
    callback_payload: Dict[str, Any],
    task_payload: Dict[str, Any],
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            callback_url,
            json=callback_payload,
            headers=_callback_headers(task_payload),
        )
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            body = None
        return {
            "status": "success",
            "http_status": response.status_code,
            "response": body,
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
            None if result.get(
                "status") == "success" else result.get("message")
        ),
        "estimate_totals": result.get("estimate_totals"),
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
        logger.info("Recovered stuck queue job queue_id=%s",
                    getattr(job, "id", None))


async def process_cloud_task_payload(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = datetime.utcnow()
    task_payload = _unwrap_task_payload(raw_payload or {})
    callback_url = str(task_payload.get("callback_url") or "").strip()
    queue_id = _extract_queue_id(task_payload)
    job = await _get_job_queue_document(queue_id)

    if job is not None:
        queue_id = str(job.id)

    if job is not None and job.status == JobQueueStatus.complete:
        result = {
            "status": "success",
            "message": "Queue record is already complete",
            "summary_file_name": job.file_name,
            "summary_file_url": job.file_url,
        }
        ended_at = datetime.utcnow()
        callback_payload = _build_result_payload(
            queue_id=queue_id,
            result=result,
            started_at=started_at,
            ended_at=ended_at,
            job=job,
        )
        if callback_url:
            result["callback"] = await _post_callback_url(
                callback_url, callback_payload, task_payload
            )
        return result

    if job is not None and job.status == JobQueueStatus.processing:
        result = {
            "status": "skipped",
            "message": "Queue record is already processing",
        }
        ended_at = datetime.utcnow()
        callback_payload = _build_result_payload(
            queue_id=queue_id,
            result=result,
            started_at=started_at,
            ended_at=ended_at,
            job=job,
        )
        if callback_url:
            result["callback"] = await _post_callback_url(
                callback_url, callback_payload, task_payload
            )
        return result

    if job is not None and not _is_job_assigned_to_current_machine(job.machine_name):
        result = {
            "status": "skipped",
            "message": (
                f"Queue record is assigned to machine '{job.machine_name}', "
                f"current machine is '{MACHINE_NAME or 'unset'}'"
            ),
        }
        ended_at = datetime.utcnow()
        callback_payload = _build_result_payload(
            queue_id=queue_id,
            result=result,
            started_at=started_at,
            ended_at=ended_at,
            job=job,
        )
        if callback_url:
            result["callback"] = await _post_callback_url(
                callback_url, callback_payload, task_payload
            )
        return result

    async with _task_execution_lock:
        result: Dict[str, Any]
        try:
            if job is not None:
                job.status = JobQueueStatus.processing
                job.updated_at = started_at
                await job.save()

            logger.info(
                "Cloud Task execution started queue_id=%s start_time=%s",
                queue_id or "none",
                started_at.isoformat(),
            )
            source_payload = await _resolve_task_source_payload(task_payload, queue_id)
            quote_record = _build_quote_record_from_task_payload(
                source_payload,
                job,
                queue_id,
            )
            psv_credentials = _extract_psv_credentials(source_payload, quote_record)
            runtime_credentials = (
                _normalize_runtime_credentials(psv_credentials)
                or _build_runtime_credentials()
            )
            _validate_runtime_credentials(runtime_credentials)

            # Call BACK_URL_STATUS_UPDATE if present
            back_url_status_update = str(task_payload.get("BACK_URL_STATUS_UPDATE") or "").strip()
            if back_url_status_update:
                logger.info(
                    "Calling BACK_URL_STATUS_UPDATE before processing: %s",
                    back_url_status_update,
                )
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        status_response = await client.post(
                            back_url_status_update,
                            json={},
                            headers=_callback_headers(task_payload),
                        )
                        status_response.raise_for_status()
                        logger.info(
                            "BACK_URL_STATUS_UPDATE response: status_code=%d body=%s",
                            status_response.status_code,
                            status_response.text[:200],
                        )
                except Exception as exc:
                    logger.exception(
                        "BACK_URL_STATUS_UPDATE call failed, proceeding anyway: %s", exc
                    )

            logger.info(
                "Cloud Task step=run_bot queue_id=%s flow_timeout_seconds=%s",
                queue_id or "none",
                DEFAULT_TIMEOUT_SECONDS,
            )
            result = await run_in_threadpool(
                run_estimate_flow,
                runtime_credentials,
                quote_record,
            )

            ended_at = datetime.utcnow()
            if job is not None:
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
                "Cloud Task execution finished queue_id=%s end_time=%s status=%s estimate_totals=%s",
                queue_id or "none",
                ended_at.isoformat(),
                result.get("status"),
                result.get("estimate_totals"),
            )

        except Exception as exc:
            logger.exception(
                "Cloud Task execution failed queue_id=%s", queue_id or "none"
            )
            ended_at = datetime.utcnow()
            result = {
                "status": "error",
                "message": str(exc),
            }
            if job is not None:
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
                job.updated_at = ended_at
                await job.save()

        callback_payload = _build_result_payload(
            queue_id=queue_id,
            result=result,
            started_at=started_at,
            ended_at=ended_at,
            job=job,
        )

        try:
            back_url_record_result = str(task_payload.get("BACK_URL_RECORD_RESULT") or "").strip()
            if back_url_record_result:
                logger.info(
                    "Posting result to BACK_URL_RECORD_RESULT: %s",
                    back_url_record_result,
                )
                # Safely collect estimate_id
                estimate_id = (
                    task_payload.get("estimate_id")
                    or (source_payload.get("estimate_id") if "source_payload" in locals() else None)
                    or (quote_record.get("estimate_id") if "quote_record" in locals() else None)
                    or result.get("estimate_id")
                )
                record_payload = {
                    "queue_id": queue_id or (str(job.id) if job is not None else ""),
                    "success": result.get("status") == "success",
                    "summary_file_name": result.get("summary_file_name"),
                    "summary_file_url": result.get("summary_file_url"),
                    "error_message": None if result.get("status") == "success" else (result.get("message") or "Bot processing failed"),
                    "estimate_totals": result.get("estimate_totals"),
                    "estimate_id": estimate_id,
                }
                async with httpx.AsyncClient(timeout=30.0) as client:
                    record_response = await client.post(
                        back_url_record_result,
                        json=record_payload,
                        headers=_callback_headers(task_payload),
                    )
                    record_response.raise_for_status()
                    logger.info(
                        "BACK_URL_RECORD_RESULT response: status_code=%d body=%s",
                        record_response.status_code,
                        record_response.text[:200],
                    )
                    result["back_url_record_result_callback"] = {
                        "status": "success",
                        "http_status": record_response.status_code,
                    }
            elif callback_url:
                logger.info(
                    "Cloud Task callback posting queue_id=%s callback_url=%s",
                    queue_id or "none",
                    callback_url,
                )
                result["callback"] = await _post_callback_url(
                    callback_url,
                    callback_payload,
                    task_payload,
                )
            elif job is not None:
                logger.info(
                    "Cloud Task callback_url missing; falling back to main server queue_id=%s",
                    queue_id,
                )
                result["main_server_callback"] = await _notify_main_server(job, result)
        except Exception as exc:
            logger.exception(
                "Cloud Task result callback failed queue_id=%s", queue_id or "none"
            )
            result["callback"] = {
                "status": "error",
                "message": str(exc),
            }
        finally:
            logger.info("Cloud Task cleanup queue_id=%s", queue_id or "none")
            _cleanup_after_job()

        return result


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
        logger.info(
            "Queue step=fetch_main_record queue_id=%s", getattr(
                job, "id", None)
        )
        payload = await fetch_main_server_record(str(job.id))
        quote = payload.get("quote") or {}
        job_data = payload.get("job") or {}
        quote_record = _build_bot_quote_record(payload, job)
        psv_credentials = (
            payload.get("tenant_credentials")
            or payload.get("psv_credentials")
            or {
                "printsmith_url": quote_record.get("printsmith_url", ""),
                "printsmith_username": quote_record.get("printsmith_username", ""),
                "printsmith_password": quote_record.get("printsmith_password", ""),
                "printsmith_company": quote_record.get("printsmith_company", ""),
            }
        )

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
        _validate_runtime_credentials(runtime_credentials)

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
        total_time_used_seconds = round(
            (ended_at - started_at).total_seconds(), 3)
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
            "Queue processing finished queue_id=%s end_time=%s total_time_used_seconds=%s status=%s estimate_totals=%s",
            getattr(job, "id", None),
            ended_at.isoformat(),
            total_time_used_seconds,
            job.status,
            result.get("estimate_totals"),
        )
        try:
            logger.info(
                "Queue step=notify_main_server queue_id=%s after bot upload and logout",
                getattr(job, "id", None),
            )
            result["main_server_callback"] = await _notify_main_server(job, result)
        except Exception as exc:
            logger.exception(
                "Failed to notify main server for queue_id=%s", getattr(
                    job, "id", None)
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
        total_time_used_seconds = round(
            (ended_at - started_at).total_seconds(), 3)
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
                "Failed to notify main server for queue_id=%s", getattr(
                    job, "id", None)
            )
        return error_result
    finally:
        logger.info("Queue cleanup queue_id=%s", getattr(job, "id", None))
        _cleanup_after_job()
