import asyncio
import contextlib
import gc
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongo import get_client
from app.v1.core.settings import (
    MAIN_SERVER_API_BASE_URL,
    MAIN_SERVER_API_TOKEN,
    MONGO_DB,
    PRINTSMITH_COMPANY,
    PRINTSMITH_PASSWORD,
    PRINTSMITH_URL,
    PRINTSMITH_USERNAME,
    QUEUE_MAX_ATTEMPTS,
    QUEUE_POLL_INTERVAL_SECONDS,
    QUEUE_PROCESSING_STALE_SECONDS,
    QUEUE_RECOVERY_INTERVAL_SECONDS,
    QUEUE_WORKER_CONCURRENCY,
)
from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS
from app.v1.modules.bot.services.estimate_service import run_estimate_flow

logger = logging.getLogger(__name__)

TASKS_COLLECTION = "tasks"
TASK_LOCKS_COLLECTION = "task_locks"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CALLBACK_PENDING = "callback_pending"
TASK_STATUS_CALLBACK_PROCESSING = "callback_processing"
TASK_STATUS_CALLBACK_FAILED = "callback_failed"
# Initial processing attempt plus at least three timeout-only retries.
MIN_QUEUE_MAX_ATTEMPTS = 4
MAX_TASK_ATTEMPTS = max(QUEUE_MAX_ATTEMPTS, MIN_QUEUE_MAX_ATTEMPTS)
CALLBACK_MAX_ATTEMPTS = 3
CALLBACK_RETRY_DELAY_SECONDS = float(
    os.getenv("QUEUE_CALLBACK_RETRY_DELAY_SECONDS", "60")
)

WORKER_ID = (
    f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
)

_mongo_client: Optional[AsyncIOMotorClient] = None
_owns_mongo_client = False
_tasks_collection: Optional[AsyncIOMotorCollection] = None
_task_locks_collection: Optional[AsyncIOMotorCollection] = None
_scheduler_task: Optional[asyncio.Task] = None
_worker_tasks: list[asyncio.Task] = []
_stop_event: Optional[asyncio.Event] = None
_local_queue: Optional[asyncio.Queue] = None
_worker_semaphore: Optional[asyncio.Semaphore] = None
_active_tasks = 0


def _now() -> datetime:
    return datetime.utcnow()


def _tasks() -> AsyncIOMotorCollection:
    if _tasks_collection is None:
        configure_queue_service()
    if _tasks_collection is None:
        raise RuntimeError("MongoDB task queue is not initialized")
    return _tasks_collection


def _task_locks() -> AsyncIOMotorCollection:
    if _task_locks_collection is None:
        configure_queue_service()
    if _task_locks_collection is None:
        raise RuntimeError("MongoDB task lock collection is not initialized")
    return _task_locks_collection


def configure_queue_service(
    mongo_client: Optional[AsyncIOMotorClient] = None,
    db_name: str = MONGO_DB,
) -> None:
    global _mongo_client, _owns_mongo_client, _tasks_collection
    global _task_locks_collection

    if mongo_client is None:
        if _mongo_client is None:
            _mongo_client = get_client()
            _owns_mongo_client = True
    else:
        _mongo_client = mongo_client
        _owns_mongo_client = False

    db = _mongo_client[db_name]
    _tasks_collection = db[TASKS_COLLECTION]
    _task_locks_collection = db[TASK_LOCKS_COLLECTION]


async def _ensure_indexes() -> None:
    collection = _tasks()
    locks_collection = _task_locks()
    await collection.create_index(
        [("queue_id", ASCENDING)],
        unique=True,
        name="uniq_queue_id",
    )
    await collection.create_index(
        [("status", ASCENDING), ("created_at", ASCENDING)],
        name="status_created_at",
    )
    await collection.create_index(
        [("status", ASCENDING), ("updated_at", ASCENDING)],
        name="status_updated_at",
    )
    await collection.create_index(
        [("status", ASCENDING), ("available_at", ASCENDING), ("created_at", ASCENDING)],
        name="status_available_created_at",
    )
    await collection.create_index(
        [("status", ASCENDING), ("callback_available_at", ASCENDING)],
        name="status_callback_available_at",
    )
    await collection.create_index(
        [("status", ASCENDING), ("lock_key_values", ASCENDING)],
        name="status_lock_key_values",
    )
    await locks_collection.create_index(
        [("lock_key", ASCENDING)],
        unique=True,
        name="uniq_lock_key",
    )
    await locks_collection.create_index(
        [("queue_id", ASCENDING)],
        name="queue_id",
    )


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


class TaskLockConflictError(Exception):
    pass


def _clean_identifier(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null"}:
        return ""
    return text


def _first_identifier(*values: Any) -> str:
    for value in values:
        text = _clean_identifier(value)
        if text:
            return text
    return ""


def _dict_value(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _extract_lock_components(
    payload: Dict[str, Any],
    queue_id: str,
) -> Dict[str, Optional[str]]:
    quote = _dict_value(payload, "quote")
    quote_record = _dict_value(payload, "quote_record")
    job = _dict_value(payload, "job")

    return {
        "queue_id": queue_id,
        "chat_id": _first_identifier(
            payload.get("chat_id"),
            quote.get("chat_id"),
            quote_record.get("chat_id"),
            job.get("chat_id"),
        ) or None,
        "quote_id": _first_identifier(
            payload.get("quote_id"),
            payload.get("_id"),
            quote.get("_id"),
            quote.get("id"),
            quote.get("quote_id"),
            quote_record.get("_id"),
            quote_record.get("id"),
            quote_record.get("quote_id"),
            job.get("quote_id"),
        ) or None,
        "estimate_id": _first_identifier(
            payload.get("estimate_id"),
            quote.get("estimate_id"),
            quote_record.get("estimate_id"),
            job.get("estimate_id"),
        ) or None,
    }


def _lock_key_values(lock_components: Dict[str, Optional[str]]) -> list[str]:
    lock_specs = (
        ("queue", lock_components.get("queue_id")),
        ("chat", lock_components.get("chat_id")),
        ("quote", lock_components.get("quote_id")),
        ("estimate", lock_components.get("estimate_id")),
    )
    values = {
        f"{name}:{str(value).strip().casefold()}"
        for name, value in lock_specs
        if _clean_identifier(value)
    }
    return sorted(values)


def _task_lock_fields(
    payload: Dict[str, Any],
    queue_id: str,
) -> Dict[str, Any]:
    lock_components = _extract_lock_components(payload, queue_id)
    return {
        "lock_keys": lock_components,
        "lock_key_values": _lock_key_values(lock_components),
    }


def _task_lock_values_from_task(task: Dict[str, Any]) -> list[str]:
    values = task.get("lock_key_values")
    if isinstance(values, list):
        return sorted(
            {
                _clean_identifier(value).casefold()
                for value in values
                if _clean_identifier(value)
            }
        )
    queue_id = _clean_identifier(task.get("queue_id"))
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    return _task_lock_fields(payload, queue_id)["lock_key_values"]


def _is_timeout_error_message(error_message: str) -> bool:
    normalized = (error_message or "").casefold()
    return any(
        fragment in normalized
        for fragment in ("timeout", "timed out", "time out")
    )


def _is_retryable_processing_error_message(error_message: str) -> bool:
    normalized = (error_message or "").casefold()
    if _is_timeout_error_message(normalized):
        return True
    return any(
        fragment in normalized
        for fragment in (
            "target page, context or browser has been closed",
            "target page has been closed",
            "context or browser has been closed",
            "browser has been closed",
            "page has been closed",
            "target closed",
            "browser closed",
            "browser crash",
            "browser crashed",
            "browser disconnected",
        )
    )


async def _post_json(
    url: str,
    payload: Dict[str, Any],
    source_payload: Dict[str, Any],
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=payload,
            headers=_callback_headers(source_payload),
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


async def fetch_main_server_record(queue_id: str) -> Dict[str, Any]:
    if not MAIN_SERVER_API_BASE_URL:
        raise HTTPException(
            status_code=500,
            detail="MAIN_SERVER_API_BASE_URL is not configured",
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


def _validate_runtime_credentials(runtime_credentials: Dict[str, str]) -> None:
    if not runtime_credentials["printsmith_url"]:
        raise HTTPException(status_code=500, detail="PRINTSMITH_URL is not configured")
    if not runtime_credentials["username"]:
        raise HTTPException(
            status_code=500,
            detail="PRINTSMITH_USERNAME is not configured",
        )
    if not runtime_credentials["password"]:
        raise HTTPException(
            status_code=500,
            detail="PRINTSMITH_PASSWORD is not configured",
        )
    if not runtime_credentials["company"]:
        raise HTTPException(
            status_code=500,
            detail="PRINTSMITH_COMPANY is not configured",
        )


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


def _build_bot_quote_record(
    payload: Dict[str, Any],
    queue_id: Optional[str] = None,
) -> Dict[str, Any]:
    quote = payload.get("quote") or {}
    raw_requirements = payload.get("requirements") or []
    tenant_credentials = payload.get("tenant_credentials") or {}
    quote_id = str(
        quote.get("_id")
        or quote.get("id")
        or quote.get("quote_id")
        or queue_id
        or ""
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
                "total": requirement.get("total", ""),
                "vendor_name": requirement.get("vendor_name", ""),
                "date": requirement.get(
                    "date",
                    requirement.get("wanted_date", requirement.get("due_date", "")),
                ),
            }
        )

    return {
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
        "company_name": quote.get("company_name", quote.get("account_name", "")),
        "street": quote.get("street", ""),
        "city": quote.get("city", ""),
        "contact_person": quote.get("contact_person", ""),
        "contact_email": quote.get("contact_email", ""),
        "contact_phone": quote.get("contact_phone", ""),
        "requirements": requirements,
        "notes": payload.get("notes", quote.get("notes", "")),
        "wanted_date": (
            payload.get("wanted_date")
            or payload.get("due_date")
            or quote.get("wanted_date")
            or quote.get("due_date")
            or ""
        ),
    }


def _build_quote_record_from_task_payload(
    payload: Dict[str, Any],
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
            normalized_record["quote_id"] = normalized_record.get("_id") or queue_id
        return normalized_record

    return _build_bot_quote_record(payload, queue_id)


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


def _cleanup_after_job() -> None:
    import logging as _logging

    bot_logger = _logging.getLogger("app.v1.modules.bot")
    for handler in list(bot_logger.handlers):
        with contextlib.suppress(Exception):
            handler.flush()

    for handler in list(_logging.getLogger().handlers):
        with contextlib.suppress(Exception):
            handler.flush()

    gc.collect()


async def enqueue_task_payload(raw_payload: Dict[str, Any]) -> Dict[str, str]:
    task_payload = _unwrap_task_payload(raw_payload or {})
    queue_id = _extract_queue_id(task_payload)
    if not queue_id:
        raise HTTPException(
            status_code=400,
            detail="Task payload must include queue_id",
        )

    now = _now()
    lock_fields = _task_lock_fields(task_payload, queue_id)
    existing_task = await _tasks().find_one(
        {"queue_id": queue_id},
        {"status": 1},
    )
    if existing_task and existing_task.get("status") != TASK_STATUS_PENDING:
        existing_status = str(existing_task.get("status") or TASK_STATUS_PENDING)
        logger.info(
            "Task already exists queue_id=%s status=%s",
            queue_id,
            existing_status,
        )
        return {"status": existing_status, "queue_id": queue_id}

    await _tasks().update_one(
        {"queue_id": queue_id},
        {
            "$setOnInsert": {
                "queue_id": queue_id,
                "status": TASK_STATUS_PENDING,
                "attempts": 0,
                "locked_by": None,
                "created_at": now,
                "available_at": now,
            },
            "$set": {
                "payload": task_payload,
                **lock_fields,
                "updated_at": now,
                "last_enqueue_at": now,
            },
        },
        upsert=True,
    )

    logger.info("Task queued queue_id=%s", queue_id)
    return {"status": "queued", "queue_id": queue_id}


async def process_cloud_task_payload(raw_payload: Dict[str, Any]) -> Dict[str, str]:
    return await enqueue_task_payload(raw_payload)


async def _acquire_task_locks(
    queue_id: str,
    lock_key_values: Sequence[str],
) -> bool:
    acquired_keys: list[str] = []
    now = _now()
    for lock_key in sorted(set(lock_key_values)):
        try:
            await _task_locks().insert_one(
                {
                    "lock_key": lock_key,
                    "queue_id": queue_id,
                    "worker_id": WORKER_ID,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            acquired_keys.append(lock_key)
        except DuplicateKeyError:
            if acquired_keys:
                await _task_locks().delete_many(
                    {
                        "queue_id": queue_id,
                        "worker_id": WORKER_ID,
                        "lock_key": {"$in": acquired_keys},
                    }
                )
            return False
    return True


async def _release_task_locks(queue_id: str, *, owner_only: bool = True) -> None:
    query = {"queue_id": queue_id}
    if owner_only:
        query["worker_id"] = WORKER_ID
    await _task_locks().delete_many(query)


async def _claim_pending_task(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    queue_id = str(candidate["queue_id"])
    task_payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    lock_fields = _task_lock_fields(task_payload, queue_id)
    lock_key_values = _task_lock_values_from_task(candidate) or lock_fields["lock_key_values"]

    if not await _acquire_task_locks(queue_id, lock_key_values):
        return None

    now = _now()
    claimed = await _tasks().find_one_and_update(
        {
            "_id": candidate["_id"],
            "queue_id": queue_id,
            "status": TASK_STATUS_PENDING,
        },
        {
            "$set": {
                "status": TASK_STATUS_PROCESSING,
                "locked_by": WORKER_ID,
                "updated_at": now,
                "processing_started_at": now,
                "lock_key_values": lock_key_values,
                "lock_keys": candidate.get("lock_keys") or lock_fields["lock_keys"],
            },
            "$inc": {"attempts": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        await _release_task_locks(queue_id)
    return claimed


async def _fetch_next_pending_task() -> Optional[Dict[str, Any]]:
    now = _now()
    candidate_limit = max(QUEUE_WORKER_CONCURRENCY * 10, 100)
    candidates = await _tasks().find(
        {
            "status": TASK_STATUS_PENDING,
            "$or": [
                {"available_at": {"$lte": now}},
                {"available_at": {"$exists": False}},
            ],
        }
    ).sort(
        [("available_at", ASCENDING), ("created_at", ASCENDING)]
    ).limit(candidate_limit).to_list(length=candidate_limit)

    for candidate in candidates:
        claimed = await _claim_pending_task(candidate)
        if claimed is not None:
            return claimed

    return None


async def _fetch_next_callback_task() -> Optional[Dict[str, Any]]:
    now = _now()
    return await _tasks().find_one_and_update(
        {
            "status": TASK_STATUS_CALLBACK_PENDING,
            "$or": [
                {"callback_available_at": {"$lte": now}},
                {"callback_available_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "status": TASK_STATUS_CALLBACK_PROCESSING,
                "locked_by": WORKER_ID,
                "updated_at": now,
                "callback_processing_started_at": now,
            },
        },
        sort=[("callback_available_at", ASCENDING), ("created_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


async def _fetch_next_task() -> Optional[Dict[str, Any]]:
    task = await _fetch_next_pending_task()
    if task is not None:
        return task
    return await _fetch_next_callback_task()


async def _recover_stale_processing_tasks() -> int:
    cutoff = _now() - timedelta(seconds=QUEUE_PROCESSING_STALE_SECONDS)
    stale_tasks = await _tasks().find(
        {
            "status": TASK_STATUS_PROCESSING,
            "updated_at": {"$lt": cutoff},
        },
        {"queue_id": 1},
    ).to_list(length=None)
    queue_ids = [
        str(task.get("queue_id"))
        for task in stale_tasks
        if _clean_identifier(task.get("queue_id"))
    ]
    if not queue_ids:
        await _recover_orphaned_task_locks(cutoff)
        await _recover_stale_callback_tasks(cutoff)
        return 0

    result = await _tasks().update_many(
        {
            "queue_id": {"$in": queue_ids},
            "status": TASK_STATUS_PROCESSING,
            "updated_at": {"$lt": cutoff},
        },
        {
            "$set": {
                "status": TASK_STATUS_PENDING,
                "locked_by": None,
                "updated_at": _now(),
                "available_at": _now(),
                "last_error": "Recovered stale processing task after worker crash",
            },
        },
    )
    await _task_locks().delete_many({"queue_id": {"$in": queue_ids}})
    await _recover_orphaned_task_locks(cutoff)
    await _recover_stale_callback_tasks(cutoff)
    if result.modified_count:
        logger.warning(
            "Recovered %s stale processing task(s)",
            result.modified_count,
        )
    return int(result.modified_count)


async def _recover_stale_callback_tasks(cutoff: datetime) -> int:
    result = await _tasks().update_many(
        {
            "status": TASK_STATUS_CALLBACK_PROCESSING,
            "updated_at": {"$lt": cutoff},
        },
        {
            "$set": {
                "status": TASK_STATUS_CALLBACK_PENDING,
                "locked_by": None,
                "updated_at": _now(),
                "callback_available_at": _now(),
                "callback_error": "Recovered stale callback delivery after worker crash",
            },
            "$unset": {"callback_processing_started_at": ""},
        },
    )
    if result.modified_count:
        logger.warning(
            "Recovered %s stale callback delivery task(s)",
            result.modified_count,
        )
    return int(result.modified_count)


async def _recover_orphaned_task_locks(cutoff: datetime) -> int:
    active_queue_ids = await _tasks().distinct(
        "queue_id",
        {"status": TASK_STATUS_PROCESSING},
    )
    query: Dict[str, Any] = {"created_at": {"$lt": cutoff}}
    if active_queue_ids:
        query["queue_id"] = {"$nin": active_queue_ids}
    result = await _task_locks().delete_many(query)
    if result.deleted_count:
        logger.warning("Deleted %s orphaned task lock(s)", result.deleted_count)
    return int(result.deleted_count)


async def recover_incomplete_jobs() -> None:
    await _recover_stale_processing_tasks()


async def _call_status_update(
    task_payload: Dict[str, Any],
    *,
    queue_id: str,
    attempt: int,
    lock_keys: Optional[Dict[str, Any]] = None,
) -> None:
    status_url = str(task_payload.get("BACK_URL_STATUS_UPDATE") or "").strip()
    if not status_url:
        return
    lock_keys = lock_keys or {}
    payload = {
        "queue_id": queue_id,
        "status": TASK_STATUS_PROCESSING,
        "attempt": attempt,
        "max_attempts": MAX_TASK_ATTEMPTS,
        "chat_id": lock_keys.get("chat_id"),
        "quote_id": lock_keys.get("quote_id"),
        "estimate_id": lock_keys.get("estimate_id") or task_payload.get("estimate_id"),
    }
    try:
        await _post_json(status_url, payload, task_payload)
    except Exception as exc:
        logger.exception("BACK_URL_STATUS_UPDATE callback failed: %s", exc)


async def _call_record_result(
    *,
    task_payload: Dict[str, Any],
    queue_id: str,
    success: bool,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    task_status: Optional[str] = None,
    attempt: Optional[int] = None,
    will_retry: bool = False,
) -> bool:
    result_url = str(task_payload.get("BACK_URL_RECORD_RESULT") or "").strip()
    callback_url = str(task_payload.get("callback_url") or "").strip()
    target_url = result_url or callback_url
    if not target_url:
        return True

    result = result or {}
    source_payload = task_payload
    estimate_id = (
        task_payload.get("estimate_id")
        or result.get("estimate_id")
    )
    payload = {
        "queue_id": queue_id,
        "success": success,
        "status": task_status or (TASK_STATUS_DONE if success else TASK_STATUS_FAILED),
        "attempt": attempt,
        "max_attempts": MAX_TASK_ATTEMPTS,
        "will_retry": will_retry,
        "summary_file_name": result.get("summary_file_name"),
        "summary_file_url": result.get("summary_file_url"),
        "summary_file_storage_key": result.get("summary_file_storage_key"),
        "error_message": None if success else (error_message or result.get("message")),
        "estimate_totals": result.get("estimate_totals"),
        "estimate_id": estimate_id,
    }
    try:
        await _post_json(target_url, payload, source_payload)
        return True
    except Exception as exc:
        logger.exception("Result callback failed queue_id=%s: %s", queue_id, exc)
        await _tasks().update_one(
            {"queue_id": queue_id},
            {
                "$set": {
                    "callback_error": str(exc),
                    "updated_at": _now(),
                }
            },
        )
        return False


async def _heartbeat(queue_id: str) -> None:
    while True:
        await asyncio.sleep(30)
        await _tasks().update_one(
            {
                "queue_id": queue_id,
                "status": TASK_STATUS_PROCESSING,
                "locked_by": WORKER_ID,
            },
            {"$set": {"updated_at": _now()}},
        )


async def _mark_task_done(
    queue_id: str,
    result: Dict[str, Any],
) -> None:
    delete_result = await _tasks().delete_many({"queue_id": queue_id})
    await _release_task_locks(queue_id, owner_only=False)
    if not delete_result.deleted_count:
        logger.warning("Completed task was not found for deletion queue_id=%s", queue_id)


def _build_callback_delivery(
    *,
    task_payload: Dict[str, Any],
    queue_id: str,
    success: bool,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    task_status: Optional[str] = None,
    attempt: Optional[int] = None,
    will_retry: bool = False,
) -> Dict[str, Any]:
    return {
        "task_payload": task_payload,
        "queue_id": queue_id,
        "success": success,
        "result": result or {},
        "error_message": error_message,
        "task_status": task_status or (TASK_STATUS_DONE if success else TASK_STATUS_FAILED),
        "attempt": attempt,
        "will_retry": will_retry,
    }


async def _mark_callback_delivery_failed_or_retry(
    *,
    task: Dict[str, Any],
    delivery: Dict[str, Any],
    error_message: str,
) -> str:
    queue_id = str(task["queue_id"])
    previous_attempts = int(task.get("callback_attempts") or 0)
    callback_attempts = previous_attempts + 1
    now = _now()
    retry_available_at = now + timedelta(seconds=CALLBACK_RETRY_DELAY_SECONDS)
    next_status = (
        TASK_STATUS_CALLBACK_PENDING
        if callback_attempts < CALLBACK_MAX_ATTEMPTS
        else TASK_STATUS_CALLBACK_FAILED
    )
    update_fields: Dict[str, Any] = {
        "status": next_status,
        "locked_by": None,
        "callback_delivery": delivery,
        "callback_attempts": callback_attempts,
        "callback_error": error_message,
        "last_error": error_message,
        "updated_at": now,
    }
    unset_fields = {"callback_processing_started_at": ""}
    if next_status == TASK_STATUS_CALLBACK_PENDING:
        update_fields["callback_available_at"] = retry_available_at
    else:
        update_fields["callback_failed_at"] = now
        update_fields["developer_review_required"] = True
        update_fields["callback_permanent_failure"] = True
        unset_fields["callback_available_at"] = ""

    await _tasks().update_one(
        {"queue_id": queue_id},
        {
            "$set": update_fields,
            "$unset": unset_fields,
            "$push": {
                "callback_failure_history": {
                    "attempt": callback_attempts,
                    "message": error_message,
                    "next_status": next_status,
                    "timestamp": now.isoformat(),
                    "worker_id": WORKER_ID,
                }
            },
        },
    )
    await _release_task_locks(queue_id, owner_only=False)
    return next_status


async def _send_or_store_record_result(
    *,
    task: Dict[str, Any],
    task_payload: Dict[str, Any],
    queue_id: str,
    success: bool,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    task_status: Optional[str] = None,
    attempt: Optional[int] = None,
    will_retry: bool = False,
) -> bool:
    delivery = _build_callback_delivery(
        task_payload=task_payload,
        queue_id=queue_id,
        success=success,
        result=result,
        error_message=error_message,
        task_status=task_status,
        attempt=attempt,
        will_retry=will_retry,
    )
    sent = await _call_record_result(**delivery)
    if sent:
        return True

    callback_error = str(
        (await _tasks().find_one({"queue_id": queue_id}, {"callback_error": 1}) or {}).get(
            "callback_error"
        )
        or "Result callback failed"
    )
    next_status = await _mark_callback_delivery_failed_or_retry(
        task=task,
        delivery=delivery,
        error_message=callback_error,
    )
    logger.warning(
        "Stored callback delivery retry queue_id=%s next_status=%s",
        queue_id,
        next_status,
    )
    return False


async def _mark_task_failed_or_retry(
    task: Dict[str, Any],
    error_message: str,
    *,
    retry_allowed: bool,
) -> str:
    queue_id = str(task["queue_id"])
    attempts = int(task.get("attempts") or 0)
    next_status = (
        TASK_STATUS_PENDING
        if retry_allowed and attempts < MAX_TASK_ATTEMPTS
        else TASK_STATUS_FAILED
    )
    now = _now()
    update_fields = {
        "status": next_status,
        "locked_by": None,
        "last_error": error_message,
        "updated_at": now,
    }
    unset_fields = {"processing_started_at": ""}
    if next_status == TASK_STATUS_PENDING:
        update_fields["available_at"] = now
        update_fields["last_retry_at"] = now
    else:
        unset_fields["available_at"] = ""
    if next_status == TASK_STATUS_FAILED:
        update_fields["failed_at"] = now

    await _tasks().update_one(
        {"queue_id": queue_id, "locked_by": WORKER_ID},
        {
            "$set": update_fields,
            "$unset": unset_fields,
            "$push": {
                "failure_history": {
                    "attempt": attempts,
                    "message": error_message,
                    "retry_allowed": retry_allowed,
                    "next_status": next_status,
                    "timestamp": now.isoformat(),
                    "worker_id": WORKER_ID,
                }
            },
        },
    )
    await _release_task_locks(queue_id)
    return next_status


async def _defer_task_for_lock_conflict(
    task: Dict[str, Any],
    error_message: str,
) -> None:
    queue_id = str(task["queue_id"])
    attempts = int(task.get("attempts") or 0)
    update: Dict[str, Any] = {
        "$set": {
            "status": TASK_STATUS_PENDING,
            "locked_by": None,
            "available_at": _now(),
            "updated_at": _now(),
            "last_error": error_message,
        },
        "$unset": {"processing_started_at": ""},
    }
    if attempts > 0:
        update["$inc"] = {"attempts": -1}

    await _tasks().update_one(
        {"queue_id": queue_id, "locked_by": WORKER_ID},
        update,
    )
    await _release_task_locks(queue_id)


async def _ensure_task_locks_for_payload(
    task: Dict[str, Any],
    source_payload: Dict[str, Any],
) -> Dict[str, Any]:
    queue_id = str(task["queue_id"])
    lock_fields = _task_lock_fields(source_payload, queue_id)
    current_values = set(_task_lock_values_from_task(task))
    desired_values = set(lock_fields["lock_key_values"])
    missing_values = sorted(desired_values - current_values)

    if missing_values and not await _acquire_task_locks(queue_id, missing_values):
        raise TaskLockConflictError(
            "Task is waiting because another job is already processing "
            "the same chat, quote, estimate, or queue id"
        )

    await _tasks().update_one(
        {"queue_id": queue_id, "locked_by": WORKER_ID},
        {
            "$set": {
                "payload": source_payload,
                **lock_fields,
                "updated_at": _now(),
            }
        },
    )
    task["payload"] = source_payload
    task["lock_keys"] = lock_fields["lock_keys"]
    task["lock_key_values"] = lock_fields["lock_key_values"]
    return lock_fields


async def _process_callback_delivery(task: Dict[str, Any], worker_name: str) -> None:
    queue_id = str(task["queue_id"])
    delivery = task.get("callback_delivery")
    if not isinstance(delivery, dict):
        await _tasks().update_one(
            {"queue_id": queue_id},
            {
                "$set": {
                    "status": TASK_STATUS_CALLBACK_FAILED,
                    "locked_by": None,
                    "developer_review_required": True,
                    "callback_permanent_failure": True,
                    "callback_error": "Missing callback delivery payload",
                    "updated_at": _now(),
                },
                "$unset": {"callback_processing_started_at": ""},
            },
        )
        return

    logger.info(
        "Worker %s sending stored result callback queue_id=%s callback_attempt=%s",
        worker_name,
        queue_id,
        int(task.get("callback_attempts") or 0) + 1,
    )
    sent = await _call_record_result(**delivery)
    if sent:
        await _mark_task_done(queue_id, delivery.get("result") or {})
        logger.info("Stored result callback delivered queue_id=%s", queue_id)
        return

    callback_error = str(
        (await _tasks().find_one({"queue_id": queue_id}, {"callback_error": 1}) or {}).get(
            "callback_error"
        )
        or "Result callback failed"
    )
    next_status = await _mark_callback_delivery_failed_or_retry(
        task=task,
        delivery=delivery,
        error_message=callback_error,
    )
    logger.warning(
        "Stored result callback failed queue_id=%s next_status=%s",
        queue_id,
        next_status,
    )


async def _process_task(task: Dict[str, Any], worker_name: str) -> None:
    queue_id = str(task["queue_id"])
    if task.get("status") == TASK_STATUS_CALLBACK_PROCESSING:
        await _process_callback_delivery(task, worker_name)
        return

    task_payload = task.get("payload") or {}
    if not isinstance(task_payload, dict):
        task_payload = {}
    callback_payload = dict(task_payload)
    attempt = int(task.get("attempts") or 0)

    heartbeat_task: Optional[asyncio.Task] = None
    try:
        logger.info(
            "Worker %s processing queue_id=%s attempt=%s",
            worker_name,
            queue_id,
            attempt,
        )
        heartbeat_task = asyncio.create_task(_heartbeat(queue_id))

        source_payload = await _resolve_task_source_payload(task_payload, queue_id)
        source_payload = {**source_payload, "queue_id": queue_id}
        callback_payload = {**source_payload, **task_payload, "queue_id": queue_id}
        lock_fields = await _ensure_task_locks_for_payload(task, source_payload)

        quote_record = _build_quote_record_from_task_payload(source_payload, queue_id)
        psv_credentials = _extract_psv_credentials(source_payload, quote_record)
        runtime_credentials = (
            _normalize_runtime_credentials(psv_credentials)
            or _build_runtime_credentials()
        )
        _validate_runtime_credentials(runtime_credentials)
        await _call_status_update(
            callback_payload,
            queue_id=queue_id,
            attempt=attempt,
            lock_keys=lock_fields["lock_keys"],
        )

        logger.info(
            "Worker %s running Playwright queue_id=%s flow_timeout_seconds=%s",
            worker_name,
            queue_id,
            DEFAULT_TIMEOUT_SECONDS,
        )
        result = await run_in_threadpool(
            run_estimate_flow,
            runtime_credentials,
            quote_record,
        )

        if result.get("status") != "success":
            error_message = result.get("message") or "Bot processing failed"
            retry_allowed = _is_retryable_processing_error_message(error_message)
            next_status = await _mark_task_failed_or_retry(
                task,
                error_message,
                retry_allowed=retry_allowed,
            )
            will_retry = next_status == TASK_STATUS_PENDING
            if not will_retry:
                await _send_or_store_record_result(
                    task=task,
                    task_payload=callback_payload,
                    queue_id=queue_id,
                    success=False,
                    result=result,
                    error_message=error_message,
                    task_status=next_status,
                    attempt=attempt,
                    will_retry=False,
                )
            logger.warning(
                "Worker %s failed queue_id=%s next_status=%s retry_allowed=%s",
                worker_name,
                queue_id,
                next_status,
                retry_allowed,
            )
            return

        callback_sent = await _send_or_store_record_result(
            task=task,
            task_payload=callback_payload,
            queue_id=queue_id,
            success=True,
            result=result,
            task_status=TASK_STATUS_DONE,
            attempt=attempt,
        )
        if callback_sent:
            await _mark_task_done(queue_id, result)
            logger.info("Worker %s completed queue_id=%s", worker_name, queue_id)
        else:
            logger.warning(
                "Worker %s completed automation queue_id=%s but callback is pending",
                worker_name,
                queue_id,
            )

    except TaskLockConflictError as exc:
        error_message = str(exc)
        await _defer_task_for_lock_conflict(task, error_message)
        logger.info(
            "Worker %s deferred queue_id=%s because record lock is busy",
            worker_name,
            queue_id,
        )

    except Exception as exc:
        error_message = str(exc) or exc.__class__.__name__
        retry_allowed = _is_retryable_processing_error_message(error_message)
        next_status = await _mark_task_failed_or_retry(
            task,
            error_message,
            retry_allowed=retry_allowed,
        )
        will_retry = next_status == TASK_STATUS_PENDING
        if not will_retry:
            await _send_or_store_record_result(
                task=task,
                task_payload=callback_payload,
                queue_id=queue_id,
                success=False,
                error_message=error_message,
                task_status=next_status,
                attempt=attempt,
                will_retry=False,
            )
        logger.exception(
            "Worker %s failed queue_id=%s next_status=%s retry_allowed=%s",
            worker_name,
            queue_id,
            next_status,
            retry_allowed,
        )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        _cleanup_after_job()


async def _worker_loop(worker_index: int) -> None:
    global _active_tasks

    assert _local_queue is not None
    assert _worker_semaphore is not None
    worker_name = f"{WORKER_ID}:worker-{worker_index}"

    while True:
        task = await _local_queue.get()
        _active_tasks += 1
        try:
            async with _worker_semaphore:
                await _process_task(task, worker_name)
        except Exception:
            logger.exception(
                "Worker %s task handler failed outside normal error flow",
                worker_name,
            )
        finally:
            _active_tasks -= 1
            _local_queue.task_done()


async def _task_scheduler() -> None:
    assert _local_queue is not None
    assert _stop_event is not None

    last_recovery_at = datetime.min
    while not _stop_event.is_set():
        try:
            now = _now()
            if (
                now - last_recovery_at
            ).total_seconds() >= QUEUE_RECOVERY_INTERVAL_SECONDS:
                await _recover_stale_processing_tasks()
                last_recovery_at = now

            claimed_or_running = _local_queue.qsize() + _active_tasks
            if claimed_or_running < QUEUE_WORKER_CONCURRENCY:
                task = await _fetch_next_task()
                if task is not None:
                    await _local_queue.put(task)
                    continue

            try:
                await asyncio.wait_for(
                    _stop_event.wait(),
                    timeout=QUEUE_POLL_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mongo task scheduler loop failed")
            await asyncio.sleep(1)


async def start_queue_workers(
    mongo_client: Optional[AsyncIOMotorClient] = None,
) -> None:
    global _scheduler_task, _worker_tasks, _stop_event, _local_queue
    global _worker_semaphore, _active_tasks

    if _scheduler_task is not None and not _scheduler_task.done():
        return

    configure_queue_service(mongo_client=mongo_client)
    await _ensure_indexes()
    await recover_incomplete_jobs()

    _stop_event = asyncio.Event()
    _active_tasks = 0
    _local_queue = asyncio.Queue(maxsize=max(QUEUE_WORKER_CONCURRENCY * 2, 1))
    _worker_semaphore = asyncio.Semaphore(QUEUE_WORKER_CONCURRENCY)
    _scheduler_task = asyncio.create_task(_task_scheduler())
    _worker_tasks = [
        asyncio.create_task(_worker_loop(index))
        for index in range(1, QUEUE_WORKER_CONCURRENCY + 1)
    ]
    logger.info(
        "Started Mongo task worker pool worker_id=%s concurrency=%s db=%s collection=%s",
        WORKER_ID,
        QUEUE_WORKER_CONCURRENCY,
        MONGO_DB,
        TASKS_COLLECTION,
    )


async def stop_queue_workers() -> None:
    global _scheduler_task, _worker_tasks, _stop_event, _local_queue
    global _worker_semaphore, _mongo_client, _tasks_collection, _owns_mongo_client
    global _task_locks_collection
    global _active_tasks

    if _stop_event is not None:
        _stop_event.set()

    tasks = []
    if _scheduler_task is not None:
        tasks.append(_scheduler_task)
    tasks.extend(_worker_tasks)

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task

    _scheduler_task = None
    _worker_tasks = []
    _stop_event = None
    _local_queue = None
    _worker_semaphore = None
    _active_tasks = 0

    if _owns_mongo_client and _mongo_client is not None:
        _mongo_client.close()
    _mongo_client = None
    _tasks_collection = None
    _task_locks_collection = None
    _owns_mongo_client = False


