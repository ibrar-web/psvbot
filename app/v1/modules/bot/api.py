import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app.v1.modules.bot.services.queue_service import process_cloud_task_payload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["bot"])


@router.get("/", summary="Bot module info")
async def bot_info(request: Request) -> Dict[str, Any]:
    current_user = getattr(request.state, "user", {})
    return {
        "status": "online",
        "module": "psvbot",
        "user": current_user,
        "mode": "cloud-task-processor",
    }


@router.post("/execute-task", summary="Execute a Cloud Tasks bot job")
async def execute_task(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Task payload must be a JSON object")
    return await process_cloud_task_payload(payload)
