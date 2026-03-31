import logging
from datetime import datetime
from typing import Any, Dict
from urllib.parse import urlparse

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from app.v1.modules.bot.dto import QueueProcessRequest, RunEstimateRequest
from app.v1.modules.bot.services.estimate_service import run_estimate_flow
from app.v1.modules.bot.services.queue_service import (
    fetch_main_server_record,
    process_job_queue_document,
    sync_job_with_main_server,
)
from app.v1.schemas.jobqueuemodel import JobQueueDocument

logger = logging.getLogger(__name__)
router = APIRouter(tags=["bot"])


@router.get("/", summary="Bot module info")
async def bot_info(request: Request) -> Dict[str, Any]:
    current_user = getattr(request.state, "user", {})
    return {
        "status": "online",
        "module": "psvbot",
        "user": current_user,
        "endpoints": {
            "run_estimate": "POST /api/v1/bot/run-estimate",
        },
    }


@router.post("/run-estimate", summary="Run the Selenium estimate flow")
async def run_estimate(payload: RunEstimateRequest, request: Request) -> Dict[str, Any]:
    current_user = getattr(request.state, "user", {})
    runtime_credentials = payload.credentials.model_dump()

    if not runtime_credentials.get("company"):
        hostname = urlparse(runtime_credentials["printsmith_url"]).hostname or ""
        runtime_credentials["company"] = hostname.split(".", 1)[0].strip()

    missing_fields = [
        name
        for name in ("printsmith_url", "username", "password", "company")
        if not str(runtime_credentials.get(name) or "").strip()
    ]
    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing runtime credential fields: {', '.join(missing_fields)}",
        )

    logger.info(
        "Received standalone /run-estimate request from=%s",
        current_user.get("sub", "unknown"),
    )

    result = await run_in_threadpool(
        run_estimate_flow,
        runtime_credentials,
        payload.quote_record,
    )
    return result


@router.post("/process-queue", summary="Process queue details sent by the main server")
async def process_queue(payload: QueueProcessRequest) -> Dict[str, Any]:
    queue_data = payload.queue.model_dump(exclude_none=True)
    runtime_credentials = payload.credentials.model_dump() if payload.credentials else {}

    if not runtime_credentials.get("company") and runtime_credentials.get("printsmith_url"):
        hostname = urlparse(runtime_credentials["printsmith_url"]).hostname or ""
        runtime_credentials["company"] = hostname.split(".", 1)[0].strip()

    result = await run_in_threadpool(
        run_estimate_flow,
        runtime_credentials,
        queue_data,
    )
    return {
        "queue": queue_data,
        "result": result,
    }


@router.post("/process-queue/{queue_id}", summary="Process one queue record from MongoDB")
async def process_queue_record(queue_id: str) -> Dict[str, Any]:
    try:
        object_id = PydanticObjectId(queue_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid queue id",
        ) from exc

    job = await JobQueueDocument.get(object_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue record not found",
        )

    result = await process_job_queue_document(job)
    return {
        "queue_id": queue_id,
        "processed_at": datetime.utcnow().isoformat(),
        "result": result,
    }


@router.get("/queue/{queue_id}/main-record", summary="Fetch queue record information from main server")
async def get_main_record(queue_id: str) -> Dict[str, Any]:
    return await fetch_main_server_record(queue_id)


@router.post("/queue/{queue_id}/sync-main-record", summary="Fetch main server record and update local queue schema")
async def sync_main_record(queue_id: str) -> Dict[str, Any]:
    try:
        object_id = PydanticObjectId(queue_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid queue id",
        ) from exc

    job = await JobQueueDocument.get(object_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue record not found",
        )

    payload = await sync_job_with_main_server(job)
    return {
        "queue_id": queue_id,
        "synced_at": datetime.utcnow().isoformat(),
        "job": job.model_dump(mode="python"),
        "main_server_payload": payload,
    }
