import logging
from typing import Any, Dict, List

from playwright.sync_api import Page

from app.v1.modules.bot.pricing_fetch.flow_runner import (
    FetchResult,
    run_pricing_flow,
)
from app.v1.modules.bot.pricing_fetch.psv_api import PsvApi

logger = logging.getLogger(__name__)

CHARGE_TREE_PATH = "chargedefn/getChargeCommands"
CHARGE_DETAILS_PATH = "chargedefn/getChargeDefnDtls?id={}"
TREE_PAGE_SIZE = 500
DETAILS_CONCURRENCY = 5

# PSV API method codes mapped to the labels shown in the PSV grid.
METHOD_LABELS = {
    "FlatRate": "Flat Rate",
    "Ask": "Always Ask",
    "JobAware": "Job Aware",
    "Cut": "Cutting Charge",
    "RateList": "Rate list",
    "Markup": "Markup",
    "SquareArea": "Square Area",
    "Linear": "Linear",
    "Ink": "Ink Charge",
}
# Markup option "apply to invoice"; every other charge applies to a job.
INVOICE_MARKUP_TYPE = "Invoice"


def _tree_request(skip: int) -> Dict[str, Any]:
    # Same body PSV's grid sends, with no filters applied.
    return {
        "gridInfo": {
            "pageSize": TREE_PAGE_SIZE,
            "skip": skip,
            "take": TREE_PAGE_SIZE,
            "filterList": [],
            "advanceFilterList": [],
        },
        "prodLocations": [],
        "salesCategories": [],
        "totalRecords": 0,
        "chargeTreeResult": [],
        "records": 0,
        "fromIndex": 0,
        "toIndex": 0,
        "deleteList": [],
        "productsList": [],
        "allowedCommandList": [],
    }


def _collect_charges(
    nodes: List[Dict[str, Any]],
    parents: Dict[str, str],
    charges: Dict[int, Dict[str, Any]],
) -> None:
    for node in nodes:
        data = node["data"]
        if data["type"] == "Charge":
            charges[data["id"]] = {**parents, "data": data}
            continue
        child_parents = {**parents, data["type"].lower(): data["name"]}
        if data["type"] == "Command":
            # The "show" toggle next to each command in Charge Definitions.
            child_parents["shown"] = bool(data.get("addCommandToJobTaskList"))
        _collect_charges(node.get("children") or [], child_parents, charges)


def _fetch_charge_tree(api: PsvApi) -> Dict[int, Dict[str, Any]]:
    charges: Dict[int, Dict[str, Any]] = {}
    skip = 0
    while True:
        tree_page = api.post(CHARGE_TREE_PATH, _tree_request(skip))
        _collect_charges(tree_page["chargeTreeResult"], {}, charges)
        skip += TREE_PAGE_SIZE
        if skip >= tree_page["totalRecords"]:
            logger.info(
                "Found %s charges (PSV reports %s records)",
                len(charges),
                tree_page["totalRecords"],
            )
            return charges


def _charge_identity(charge: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "command": charge.get("command", ""),
        "category": charge.get("category", ""),
        "name": " ".join(charge["data"]["name"].split()),
    }


def _build_charge(
    charge: Dict[str, Any], details: Dict[str, Any]
) -> Dict[str, Any]:
    data = charge["data"]
    method = data.get("chargeMethod") or ""
    is_invoice = details.get("markupType") == INVOICE_MARKUP_TYPE
    return {
        **_charge_identity(charge),
        "method": METHOD_LABELS.get(method, method),
        "charge_level": "invoice" if is_invoice else "job",
        "production_location": data.get("prodLocation") or "",
        "sales_category": data.get("salesCategory") or "",
        "price": details.get("rate"),
    }


def _fetch_job_charges_pricing(
    page: Page, task_payload: Dict[str, Any]
) -> FetchResult:
    logger.info("Fetching job charges pricing from PSV API")
    api = PsvApi(page)
    all_charges = _fetch_charge_tree(api)
    charges = {
        charge_id: charge
        for charge_id, charge in all_charges.items()
        if charge.get("shown")
    }
    logger.info(
        "Total job charges: %s, shown (command toggle on): %s",
        len(all_charges),
        len(charges),
    )
    results = api.get_many(
        [CHARGE_DETAILS_PATH.format(charge_id) for charge_id in charges],
        DETAILS_CONCURRENCY,
    )

    rows, failed = [], []
    for charge_id, charge, result in zip(
        charges, charges.values(), results
    ):
        if result["ok"]:
            rows.append(_build_charge(charge, result["data"]))
        else:
            failed.append(
                {
                    "id": charge_id,
                    **_charge_identity(charge),
                    "reason": result["reason"],
                }
            )

    if not rows:
        raise RuntimeError(
            f"All {len(failed)} charge detail requests failed, "
            f"first reason: {failed[0]['reason']}"
        )
    logger.info(
        "Fetched %s charges, %s failed", len(rows), len(failed)
    )
    return rows, failed


def run_job_charges_pricing_flow(
    tenant_credentials: Dict[str, Any],
    task_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return run_pricing_flow(
        "Job charges pricing",
        _fetch_job_charges_pricing,
        tenant_credentials,
        task_payload,
    )
