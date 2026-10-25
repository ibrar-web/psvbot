import logging
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page

from app.v1.modules.bot.pricing_fetch.flow_runner import (
    FetchResult,
    run_pricing_flow,
    split_known,
)
from app.v1.modules.bot.pricing_fetch.job_charges_pricing import (
    INVOICE_MARKUP_TYPE,
    METHOD_LABELS,
)
from app.v1.modules.bot.pricing_fetch.psv_api import (
    PsvApi,
    index_jsog_objects,
    resolve_jsog_ref,
)

logger = logging.getLogger(__name__)

STOCK_LIST_PATH = (
    "stockdefinition/getStockDefinitionList?pricingMethod={}&doPrint=false"
)
# lock=false so reading a stock never locks it for PSV users.
STOCK_DETAILS_PATH = (
    "stockdefinition/getStockDefinitionById"
    "?stockId={}&lock=false&doDuplicate=false"
)
LIST_PAGE_SIZE = 100
DETAILS_CONCURRENCY = 5
# The Pricing tab's blank sheets table has six quantity/price rows.
BLANK_SHEET_ROWS = range(1, 7)
BLANK_SHEET_PRICE_QTY = 1

# Stocks don't store an estimator: PSV's job stock picker filters the stock
# list by the job's estimator. These two split the stocks (Large Format/Rigid
# groups vs Paper); BW and sheet press return the same stocks as color, and
# the other estimators don't filter.
ESTIMATORS = ("LFCopierDefinitions", "ColorCopierDefinitions")


def _list_request(skip: int) -> Dict[str, Any]:
    # Same body PSV's grid sends with only the Inventory Stock filter on.
    return {
        "gridInfo": {
            "sortField": "name",
            "sortDir": "desc",
            "take": LIST_PAGE_SIZE,
            "skip": skip,
            "pageSize": 0,
            "filterList": [],
            "advanceFilterList": [
                {"fieldName": "filterInvStock", "fieldValue": True}
            ],
            "top": 0,
        },
        "walkin": False,
        "fetchAll": False,
        "isInventory": True,
        "isStockExpired": True,
        "isPriceExpired": True,
    }


def _fetch_stock_list(
    api: PsvApi, estimator: str = ""
) -> List[Dict[str, Any]]:
    stocks: List[Dict[str, Any]] = []
    skip = 0
    while True:
        list_page = api.post(
            STOCK_LIST_PATH.format(estimator), _list_request(skip)
        )
        page_stocks = list_page["stockDefinitionList"]
        stocks.extend(page_stocks)
        skip += LIST_PAGE_SIZE
        total = list_page["currentTotalResultsCount"]
        if not page_stocks or skip >= total:
            logger.info(
                "Found %s inventory stocks for estimator '%s' "
                "(PSV reports %s)",
                len(stocks),
                estimator,
                total,
            )
            return stocks


def _fetch_stock_estimators(api: PsvApi) -> Dict[int, str]:
    stock_estimators: Dict[int, str] = {}
    for estimator in ESTIMATORS:
        for stock in _fetch_stock_list(api, estimator):
            stock_estimators.setdefault(stock["id"], estimator)
    return stock_estimators


def _blank_sheet_price(details: Dict[str, Any]) -> Optional[float]:
    for row in BLANK_SHEET_ROWS:
        if details.get(f"blankSheetqty{row}") == BLANK_SHEET_PRICE_QTY:
            return details.get(f"blankSheetPrice{row}")
    return None


def _name_of(item: Any, objects: Dict[str, Dict[str, Any]]) -> str:
    return resolve_jsog_ref(item, objects).get("name") or ""


def _build_stock_charge(
    charge: Dict[str, Any], objects: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    category = resolve_jsog_ref(charge.get("parent"), objects)
    method = charge.get("method") or ""
    is_invoice = charge.get("markupType") == INVOICE_MARKUP_TYPE
    return {
        "command": _name_of(category.get("parent"), objects),
        "category": category.get("name") or "",
        "name": " ".join((charge.get("name") or "").split()),
        "method": METHOD_LABELS.get(method, method),
        "charge_level": "invoice" if is_invoice else "job",
        "production_location": _name_of(charge.get("location"), objects),
        "sales_category": _name_of(charge.get("salesCategory"), objects),
        "price": charge.get("rate"),
    }


def _build_stock(details: Dict[str, Any], estimator: str) -> Dict[str, Any]:
    objects = index_jsog_objects(details)
    size = _name_of(details.get("parentsize"), objects)
    stock_charges = [
        resolve_jsog_ref(
            resolve_jsog_ref(link, objects).get("charge"), objects
        )
        for link in details.get("charges") or []
    ]
    return {
        "name": details.get("name") or "",
        "size": size,
        "price": _blank_sheet_price(details),
        "estimator": estimator,
        "stock_level_charge": [
            _build_stock_charge(charge, objects)
            for charge in stock_charges
            if charge
        ],
    }


def _stock_label(stock: Dict[str, Any]) -> str:
    return stock.get("name") or ""


def _fetch_stock_details(
    api: PsvApi, stocks: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    stock_estimators = _fetch_stock_estimators(api)
    results = api.get_many(
        [STOCK_DETAILS_PATH.format(stock["id"]) for stock in stocks],
        DETAILS_CONCURRENCY,
    )

    rows, failed = [], []
    for stock, result in zip(stocks, results):
        if result["ok"]:
            rows.append(
                _build_stock(
                    result["data"], stock_estimators.get(stock["id"], "")
                )
            )
        else:
            failed.append(
                {
                    "id": stock["id"],
                    "name": stock.get("name") or "",
                    "reason": result["reason"],
                }
            )

    if failed and not rows:
        raise RuntimeError(
            f"All {len(failed)} stock detail requests failed, "
            f"first reason: {failed[0]['reason']}"
        )
    logger.info(
        "Fetched %s stocks (%s without a qty %s blank sheet price, "
        "%s with stock level charges, %s with an estimator), %s failed",
        len(rows),
        sum(row["price"] is None for row in rows),
        BLANK_SHEET_PRICE_QTY,
        sum(bool(row["stock_level_charge"]) for row in rows),
        sum(bool(row["estimator"]) for row in rows),
        len(failed),
    )
    return rows, failed


def _fetch_stock_material_pricing(
    page: Page, task_payload: Dict[str, Any]
) -> FetchResult:
    logger.info("Fetching stock material pricing from PSV API")
    api = PsvApi(page)
    rows, failed = _fetch_stock_details(api, _fetch_stock_list(api))
    return rows, failed, {}


def _refresh_stock_material_pricing(
    page: Page, task_payload: Dict[str, Any]
) -> FetchResult:
    logger.info("Refreshing stock material pricing from PSV API")
    api = PsvApi(page)
    stocks = {stock["id"]: stock for stock in _fetch_stock_list(api)}
    new_stocks, extra = split_known(stocks, _stock_label, task_payload)
    rows, failed = _fetch_stock_details(api, list(new_stocks.values()))
    return rows, failed, extra


def run_stock_material_pricing_flow(
    tenant_credentials: Dict[str, Any],
    task_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return run_pricing_flow(
        "Stock material pricing",
        _fetch_stock_material_pricing,
        tenant_credentials,
        task_payload,
    )


def run_stock_material_refresh_flow(
    tenant_credentials: Dict[str, Any],
    task_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return run_pricing_flow(
        "Stock material refresh",
        _refresh_stock_material_pricing,
        tenant_credentials,
        task_payload,
    )
