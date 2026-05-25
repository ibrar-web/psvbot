import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Query

from app.v1.modules.bot.services.queue_service import enqueue_task_payload

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


@router.post("/execute-task", summary="Enqueue a Cloud Tasks bot job")
async def enqueue_task(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="Task payload must be a JSON object"
        )
    return await enqueue_task_payload(payload)


# @router.post("/execute-task", summary="Enqueue a Cloud Tasks bot job")
# async def execute_task(request: Request) -> Dict[str, Any]:
#     return await enqueue_task(request)


@router.get("/execute-test-task", summary="Execute bot job with test data")
async def execute_test_task(
    id: str = Query(..., description="Test payload id")
) -> Dict[str, Any]:

    print("execute_test_task")

    testdata_path = Path(__file__).parent / "testdata.json"
    print(f'file addredd :{testdata_path}')
    if not testdata_path.exists():
        raise HTTPException(status_code=404, detail="testdata.json file not found")
  
    try:
        content = testdata_path.read_text(encoding="utf-8")
        all_payloads = json.loads(content)
        print(all_payloads)
        # read payload against id
        payload = all_payloads.get(id)

        if not payload:
            raise HTTPException(
                status_code=404, detail=f"No test payload found for id: {id}"
            )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to parse testdata.json: {exc}"
        )

    logger.info(f"Parsed test payload for id={id}: {payload}")

    return await enqueue_task_payload(payload)

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Query

from app.v1.modules.bot.services.queue_service import enqueue_task_payload

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


@router.post("/enqueue-task", summary="Enqueue a Cloud Tasks bot job")
async def enqueue_task(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="Task payload must be a JSON object"
        )
    return await enqueue_task_payload(payload)


@router.post("/execute-task", summary="Enqueue a Cloud Tasks bot job")
async def execute_task(request: Request) -> Dict[str, Any]:
    return await enqueue_task(request)


@router.get("/execute-test-task", summary="Execute bot job with test data")
async def execute_test_task(
    id: str = Query(..., description="Test payload id")
) -> Dict[str, Any]:

    print("execute_test_task")

    testdata_path = Path(__file__).parent / "testdata.json"
    print(f'file addredd :{testdata_path}')
    if not testdata_path.exists():
        raise HTTPException(status_code=404, detail="testdata.json file not found")
  
    try:
        content = testdata_path.read_text(encoding="utf-8")
        all_payloads = json.loads(content)
        print(all_payloads)
        # read payload against id
        payload = all_payloads.get(id)

        if not payload:
            raise HTTPException(
                status_code=404, detail=f"No test payload found for id: {id}"
            )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to parse testdata.json: {exc}"
        )

    logger.info(f"Parsed test payload for id={id}: {payload}")

    return await enqueue_task_payload(payload)
