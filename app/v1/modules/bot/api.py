import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Query

from app.v1.core.settings import MACHINE_NAME
from app.v1.modules.bot.services.queue_service import enqueue_task_payload
from app.v1.modules.bot.task_types import TaskType


def _build_envelope(task_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_type": task_type,
        "machine_name": MACHINE_NAME or None,
        "data": data,
    }

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


def _load_test_payload(case: str, id: str) -> Dict[str, Any]:
    """testdata.json is grouped by task_type keyword (the same keyword sent as
    `task_type` in a real payload), then by test id within that case."""
    testdata_path = Path(__file__).parent / "testdata.json"
    if not testdata_path.exists():
        raise HTTPException(status_code=404, detail="testdata.json file not found")

    try:
        content = testdata_path.read_text(encoding="utf-8")
        all_payloads = json.loads(content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to parse testdata.json: {exc}"
        )

    case_payloads = all_payloads.get(case)
    if not isinstance(case_payloads, dict):
        raise HTTPException(
            status_code=404, detail=f"No test payloads found for case: {case}"
        )

    payload = case_payloads.get(id)
    if not payload:
        raise HTTPException(
            status_code=404, detail=f"No test payload found for case={case} id={id}"
        )

    return payload


@router.get("/execute-test-task", summary="Execute bot job with test data")
async def execute_test_task(
    id: str = Query(..., description="Test payload id"),
) -> Dict[str, Any]:
    data = _load_test_payload(TaskType.CREATE_ESTIMATE.value, id)
    envelope = _build_envelope(TaskType.CREATE_ESTIMATE.value, data)

    logger.info(f"Parsed create_estimate test payload for id={id}: {envelope}")

    return await enqueue_task_payload(envelope)


_ESTIMATE_HISTORY_TASK_TYPES = {
    TaskType.ESTIMATE_HISTORY_EXPORT.value,
    TaskType.ESTIMATE_HISTORY_LOOKUP.value,
}


@router.get(
    "/execute-test-estimate-history-task",
    summary="Execute estimate history bot job with test data (bulk export or single-record lookup)",
)
async def execute_test_estimate_history_task(
    id: str = Query(..., description="Test payload id"),
    case: str = Query(
        ...,
        description=(
            "task_type keyword: 'estimate_history_export' or 'estimate_history_lookup'"
        ),
    ),
) -> Dict[str, Any]:
    normalized_case = (case or "").strip().lower()
    if normalized_case not in _ESTIMATE_HISTORY_TASK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown case '{case}'; expected one of: "
                f"{', '.join(sorted(_ESTIMATE_HISTORY_TASK_TYPES))}"
            ),
        )

    data = _load_test_payload(normalized_case, id)
    envelope = _build_envelope(normalized_case, data)

    logger.info(f"Parsed {normalized_case} test payload for id={id}: {envelope}")

    return await enqueue_task_payload(envelope)
