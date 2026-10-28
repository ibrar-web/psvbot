import logging
from typing import Any, Dict

from playwright.sync_api import Page

from app.v1.modules.bot.pricing_fetch.flow_runner import (
    FetchResult,
    run_pricing_flow,
)
from app.v1.modules.bot.pricing_fetch.psv_api import (
    PsvApi,
    index_jsog_objects,
    resolve_jsog_ref,
)

logger = logging.getLogger(__name__)

# Same list PSV's Create Estimate dialog shows: only the pricing methods
# marked "used" in Preferences > Pricing Methods, in their display order.
USED_PRICING_METHODS_PATH = "preferences/getUsedPricingMethod"


def _build_job_method(pricing_method: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": pricing_method.get("id"),
        "job_method": " ".join((pricing_method.get("title") or "").split()),
        "estimator": pricing_method.get("estimator") or "",
    }


def _fetch_job_methods(
    page: Page, task_payload: Dict[str, Any]
) -> FetchResult:
    logger.info("Fetching job methods from PSV API")
    result = PsvApi(page).get_many([USED_PRICING_METHODS_PATH], 1)[0]
    if not result["ok"]:
        raise RuntimeError(
            f"PSV API {USED_PRICING_METHODS_PATH} failed: {result['reason']}"
        )
    pricing_methods = result["data"] or []
    if not pricing_methods:
        raise RuntimeError("PSV returned no used job methods")

    objects = index_jsog_objects(pricing_methods)
    rows = [
        _build_job_method(resolve_jsog_ref(item, objects))
        for item in sorted(
            pricing_methods,
            key=lambda item: resolve_jsog_ref(item, objects).get("orderby", 0),
        )
    ]
    logger.info("Fetched %s job methods", len(rows))
    return rows, [], {}


def run_job_methods_flow(
    tenant_credentials: Dict[str, Any],
    task_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return run_pricing_flow(
        "Job methods",
        _fetch_job_methods,
        tenant_credentials,
        task_payload,
    )
