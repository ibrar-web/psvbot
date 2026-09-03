import logging
import time
from typing import Any, Dict, Optional

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.v1.modules.bot import csv_logger
from app.v1.modules.bot.config import DEBUG
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

logger = logging.getLogger(__name__)


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][InvoiceHistoryLookup] {message}")
    logger.info(message)


def _reshape_job_item(item: Dict[str, Any], *, parts: Optional[list] = None) -> Dict[str, Any]:
    """Reshape one scraped job item (or one multi-part part, same shape)
    into the requirement schema used by create_estimate's data model (see
    testdata.json's "create_estimate" entries / InvoicePage._build_job_data):
    description, stock_search, quantity, size, sides, job_method,
    job_charges. Key names match that schema exactly; the values behind
    "size"/"sides"/"product" come from the closest equivalent read off Job
    Details: size = Finish Size, sides = the active Print button
    (Simplex/Duplex), product = Stock (the Product dropdown is frequently
    empty on real invoices; Stock carries the actual meaningful value).
    Extra scraped fields (stock_color, location, job_comment,
    unit_per_side, price, notes, job_name) are kept alongside since they
    carry real information beyond the original schema.

    `parts`, when given, is inserted right after job_charges — mirrors a
    Multi-Part job's own "parts" key (see _build_job_data on the write
    side), each part reshaped through this same function.
    """
    requirement: Dict[str, Any] = {
        "job_name": item.get("job_name", ""),
        "description": item.get("description", ""),
        "stock_search": item.get("stock", ""),
        "quantity": item.get("quantity", ""),
        "size": item.get("finish_size", ""),
        "sides": item.get("sides", ""),
        "job_method": item.get("job_method", ""),
        "job_charges": item.get("job_charges", []),
    }
    if parts is not None:
        requirement["parts"] = parts
    requirement.update(
        {
            "product": item.get("stock", ""),
            "stock_color": item.get("stock_color", ""),
            "location": item.get("location", ""),
            "job_comment": item.get("job_comment", ""),
            "unit_per_side": item.get("unit_per_side", ""),
            "price": item.get("price", ""),
            "notes": item.get("notes", ""),
            # Sublet-family only (Sublet/Sublet Printing/Promo/Signs/Sign
            # Install) — empty string for every other job method.
            "vendor_name": item.get("vendor_name", ""),
            "agent_total": item.get("agent_total", ""),
        }
    )
    return requirement


def _to_requirements_format(job_items: list, other_charges: list) -> list:
    """Reshape scraped job items into requirements (see _reshape_job_item).

    Charges Only jobs are excluded entirely — their data already lives in
    other_charges (see _build_other_charges), so keeping them here too
    would just duplicate it. other_charges is attached to the first
    remaining (non-Charges-Only) job — right after its own job_charges —
    rather than as a separate top-level key.

    A Multi-Part job's own "parts" list is reshaped the same way, part by
    part — including a Charges Only part, which is NOT excluded/hoisted
    the way a top-level Charges Only job is; it stays inside "parts" as-is,
    matching the write side's own data shape.
    """
    requirements = []
    for item in job_items:
        if (item.get("job_method") or "").strip().lower() == "charges only":
            continue
        is_multipart = (item.get("job_method") or "").strip().lower() == "multi-part"
        parts = (
            [_reshape_job_item(part) for part in item.get("parts", [])]
            if is_multipart
            else None
        )
        requirement = _reshape_job_item(item, parts=parts)
        if not requirements and other_charges:
            requirement["other_charges"] = other_charges
        requirements.append(requirement)
    return requirements


def _build_other_charges(job_items: list) -> list:
    """Top-level other_charges: one summary object per "Charges Only" job
    on the invoice (there can be more than one). A Charges Only job isn't
    a real print job — it's a charge wearing a job wrapper — so each one
    contributes its own description/quantity/price as the charge, with
    its individual charge lines nested inside as "job_charges". Distinct
    from the top-level "direct_charges" (read straight off the
    Estimate/Invoice Summary rows, no clicking involved).
    """
    other_charges = []
    for item in job_items:
        if (item.get("job_method") or "").strip().lower() != "charges only":
            continue
        other_charges.append(
            {
                "charge_name": item.get("description", ""),
                "quantity": item.get("quantity", ""),
                "charge_price": item.get("price", ""),
                "job_charges": list(item.get("job_charges", [])),
            }
        )
    return other_charges


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

            current_step = "format_result"
            _ensure_within_timeout(started_at, current_step)
            job_items = scraped.get("job_items", [])
            # direct_charges: invoice-wide charges read straight off the
            # Estimate/Invoice Summary tree table rows, no clicking involved.
            direct_charges = scraped.get("other_charges", [])
            # other_charges: one summary object per Charges Only job,
            # nested inside the first requirement (see
            # _to_requirements_format) rather than kept as its own
            # top-level key.
            other_charges = _build_other_charges(job_items)
            requirements = _to_requirements_format(job_items, other_charges)
            formatted = {
                "invoice_id": invoice_id,
                "requirements": requirements,
                "direct_charges": direct_charges,
            }
            logger.info("Invoice history lookup result for invoice_id=%s: %s", invoice_id, formatted)

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
                # requirements (not the raw scraped job_items) so this
                # matches create_estimate's own incoming "requirements"
                # shape (job_method/description/stock_search/size/sides/
                # quantity/job_charges) — Digital Color, Multi-Part, etc.
                "job_items": requirements,
                "direct_charges": direct_charges,
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
