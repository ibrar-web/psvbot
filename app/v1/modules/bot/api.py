import json
import logging
from pathlib import Path
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


@router.get("/execute-test-task", summary="Execute bot job with test data")
async def execute_test_task() -> Dict[str, Any]:
    testdata_path = Path(__file__).parent / "testdata.py"
    if not testdata_path.exists():
        raise HTTPException(status_code=404, detail="testdata.py file not found")
    
    try:
        content = testdata_path.read_text(encoding="utf-8")
        payload = json.loads(content)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse testdata.py as JSON: {exc}"
        )
        
    return await process_cloud_task_payload(payload)
