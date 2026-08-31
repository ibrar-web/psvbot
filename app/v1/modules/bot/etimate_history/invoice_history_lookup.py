import logging
import time
from typing import Any, Dict, Optional

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.v1.modules.bot import csv_logger
from app.v1.modules.bot.config import DEBUG, INVOICE_DETAIL_STORAGE_ROOT
from app.v1.modules.bot.session_runner import (
    _cleanup_browser,
    _ensure_browser_and_login,
    _ensure_within_timeout,
    _logout_if_possible,
)
from app.v1.modules.bot.pages.login_page import InvalidLoginCredentialsError
from app.v1.modules.bot.etimate_history.pages.invoice_history_lookup_page import (
    InvoiceHistoryLookupPage,
)
from app.v1.modules.bot.etimate_history.public_storage import store_json_publicly

logger = logging.getLogger(__name__)


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][InvoiceHistoryLookup] {message}")
    logger.info(message)


def _to_requirements_format(job_items: list) -> list:
    """Reshape scraped job items into the same requirement shape used by
    create_estimate's data model (see testdata.json's "create_estimate"
    entries / InvoicePage._build_job_data): description, stock_search,
    quantity, size, sides, job_method, job_charges. Key names match that
    schema exactly; the values behind "size"/"sides"/"product" come from
    the closest equivalent read off Job Details: size = Finish Size,
    sides = the active Print button (Simplex/Duplex), product = Stock
    (the Product dropdown is frequently empty on real invoices; Stock
    carries the actual meaningful value). Extra scraped fields
    (stock_color, location, job_comment, unit_per_side, price, notes,
    job_name) are kept alongside since they carry real information beyond
    the original schema.
    """
    requirements = []
    for item in job_items:
        requirements.append(
            {
                "job_name": item.get("job_name", ""),
                "description": item.get("description", ""),
                "stock_search": item.get("stock", ""),
                "quantity": item.get("quantity", ""),
                "size": item.get("finish_size", ""),
                "sides": item.get("sides", ""),
                "job_method": item.get("job_method", ""),
                "job_charges": item.get("job_charges", []),
                "product": item.get("stock", ""),
                "stock_color": item.get("stock_color", ""),
                "location": item.get("location", ""),
                "job_comment": item.get("job_comment", ""),
                "unit_per_side": item.get("unit_per_side", ""),
                "price": item.get("price", ""),
                "notes": item.get("notes", ""),
            }
        )
    return requirements


def _store_invoice_detail_json_publicly(
    formatted: Dict[str, Any],
    *,
    invoice_id: str,
    tenant_id: str,
    queue_id: str,
) -> Dict[str, Optional[str]]:
    result = store_json_publicly(
        formatted,
        subfolder=INVOICE_DETAIL_STORAGE_ROOT,
        tenant_id=tenant_id,
        queue_id=queue_id,
        file_name=f"invoice_{invoice_id}.json",
    )
    return {
        "detail_file_name": result["file_name"],
        "detail_file_local_path": result["file_local_path"],
        "detail_file_url": result["file_url"],
    }


def run_invoice_history_lookup_flow(
    tenant_credentials: Optional[Dict[str, Any]] = None,
    task_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search the Estimate History grid for one invoice_id, open it, and
    scrape its Estimate Summary tree table: invoice-wide charge rows are
    read directly, and each job row is opened in Job Details, scraped, and
    the flow returns to Estimate Summary before continuing.

    Handles a locked record transparently: dismisses the "locked by user X"
    dialog, releases all record locks via the shared BasePage helpers, and
    retries opening the record before giving up.
    """
    tenant_credentials = tenant_credentials or {}
    task_payload = task_payload or {}
    username = str(tenant_credentials.get("username") or "").strip()
    password = str(tenant_credentials.get("password") or "").strip()
    company = str(tenant_credentials.get("company") or "").strip()
    base_url = str(tenant_credentials.get("printsmith_url") or "").strip()
    invoice_id = str(task_payload.get("invoice_id") or "").strip()
    queue_id = str(task_payload.get("queue_id") or "").strip() or "manual"
    tenant_id = str(task_payload.get("tenant_id") or "adhoc").strip() or "adhoc"

    if not username or not password:
        return {
            "status": "error",
            "message": "Missing PrintSmith username or password",
        }
    if not base_url:
        return {
            "status": "error",
            "message": "Missing PrintSmith base url",
        }
    if not invoice_id:
        return {
            "status": "error",
            "message": "Missing invoice_id for history lookup",
        }

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    flow_failed = False
    current_step = "starting"
    started_at = time.monotonic()
    logout_succeeded = False
    logout_error: Optional[str] = None

    try:
        with sync_playwright() as playwright:
            csv_logger.init()
            _debug(f"Starting invoice history lookup flow for invoice_id={invoice_id}")

            current_step = "login"
            _ensure_within_timeout(started_at, current_step)
            browser, context, page = _ensure_browser_and_login(
                playwright,
                base_url=base_url,
                username=username,
                password=password,
                company=company,
            )

            current_step = "open_estimate_history"
            _ensure_within_timeout(started_at, current_step)
            history_page = InvoiceHistoryLookupPage(page)
            history_page.open_from_quick_access()
            _debug(f"Estimate History grid opened. URL: {page.url}")

            current_step = "search_and_open_record"
            _ensure_within_timeout(started_at, current_step)
            history_page.search_and_open_by_invoice_id(invoice_id)
            _debug(f"Invoice record opened. URL: {page.url}")

            current_step = "scrape_invoice"
            _ensure_within_timeout(started_at, current_step)
            scraped = history_page.scrape_invoice()
            _debug(
                f"Scraped {len(scraped.get('job_items', []))} job item(s), "
                f"{len(scraped.get('other_charges', []))} other charge(s)"
            )

            current_step = "store_details"
            _ensure_within_timeout(started_at, current_step)
            formatted = {
                "invoice_id": invoice_id,
                "requirements": _to_requirements_format(scraped.get("job_items", [])),
                "other_charges": scraped.get("other_charges", []),
            }
            store_result = _store_invoice_detail_json_publicly(
                formatted, invoice_id=invoice_id, tenant_id=tenant_id, queue_id=queue_id
            )

            current_step = "logout"
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)

            return {
                "status": "success",
                "message": "Invoice job details scraped and stored",
                "step": current_step,
                "invoice_id": invoice_id,
                "current_url": page.url,
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
                "job_items": scraped.get("job_items", []),
                "other_charges": scraped.get("other_charges", []),
                **store_result,
            }

    except InvalidLoginCredentialsError as exc:
        flow_failed = True
        logger.warning("Invoice history lookup stopped due to invalid login credentials")
        return {
            "status": "error",
            "message": str(exc),
            "step": current_step,
            "logout_succeeded": False,
            "logout_error": None,
        }

    except PlaywrightTimeoutError as exc:
        flow_failed = True
        if page is not None:
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)
        logger.exception("Invoice history lookup failed with Playwright timeout error")
        return {
            "status": "error",
            "message": str(exc),
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
        }

    except Exception as exc:
        flow_failed = True
        if page is not None:
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)
        logger.exception("Invoice history lookup failed")
        return {
            "status": "error",
            "message": f"Unexpected error: {exc}",
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
        }

    finally:
        _cleanup_browser(
            browser, context, page,
            flow_failed=flow_failed,
            logout_succeeded=logout_succeeded,
            logout_error=logout_error,
        )
        del browser, context, page
