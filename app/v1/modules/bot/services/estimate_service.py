import gc
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.v1.common.storage_service import (
    build_storage_key,
    upload_bytes_to_storage,
)
from app.v1.core.settings import BUCKET_NAME, QUOTE_SUMMARY_STORAGE_ROOT
from app.v1.modules.bot.config import DEBUG, DEFAULT_TIMEOUT_SECONDS
from app.v1.modules.bot import csv_logger
from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.session_runner import (
    _build_quick_access_url,
    _ensure_browser_and_login,
    _ensure_within_timeout,
    _cleanup_browser,
    _logout_if_possible,
    _navigate_with_recovery,
    _recover_session_from_home,
)
from app.v1.modules.bot.pages.estimate_page import EstimatePage
from app.v1.modules.bot.pages.estimate_selection_page import (
    EstimateLockedError,
    EstimateSelectionPage,
)
from app.v1.modules.bot.pages.invoice_page.invoice_page import InvoicePage
from app.v1.modules.bot.pages.invoice_page.estimated_summary import EstimatedSummaryTab
from app.v1.modules.bot.pages.invoice_page.job_details import InvalidStockSearchError
from app.v1.modules.bot.pages.login_page import InvalidLoginCredentialsError
from app.v1.modules.bot.pages.new_estimate_page import NewEstimatePage

logger = logging.getLogger(__name__)
FLOW_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS

def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][Service] {message}")
    logger.info(message)


def _build_summary_output_path(quote_record: Optional[Dict[str, Any]], summary_file_name: str) -> Path:
    root = Path(QUOTE_SUMMARY_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root / summary_file_name


def _upload_summary_file(
    invoice_path: Path,
    quote_record: Optional[Dict[str, Any]],
) -> Dict[str, Optional[str]]:
    summary_file_name = invoice_path.name
    saved_summary_path = _build_summary_output_path(quote_record, summary_file_name)
    shutil.copy2(invoice_path, saved_summary_path)

    tenant_id = str((quote_record or {}).get("tenant_id") or "adhoc").strip() or "adhoc"
    quote_id = str(
        (quote_record or {}).get("_id")
        or (quote_record or {}).get("id")
        or (quote_record or {}).get("quote_id")
        or "manual"
    ).strip() or "manual"

    folder_prefix = f"{QUOTE_SUMMARY_STORAGE_ROOT}/{tenant_id}/{quote_id}"
    storage_key = build_storage_key(folder_prefix, summary_file_name)
    upload_bytes_to_storage(
        key=storage_key,
        content=invoice_path.read_bytes(),
        content_type="application/pdf",
        metadata={
            "tenant_id": tenant_id,
            "quote_id": quote_id,
        },
    )

    try:
        saved_summary_path.unlink(missing_ok=True)
    except Exception:
        logger.exception(
            "Failed to remove local summary copy after GCS upload: %s",
            saved_summary_path,
        )

    return {
        "summary_file_name": summary_file_name,
        "summary_file_path": None,
        "summary_folder_prefix": folder_prefix,
        "summary_file_storage_key": storage_key,
        "summary_file_url": storage_key,
    }


def _cleanup_local_invoice_file(invoice_path: Optional[Path]) -> None:
    if invoice_path is None:
        return
    try:
        invoice_path.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to delete temporary invoice file")
        return

    try:
        parent = invoice_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        logger.exception("Failed to delete temporary invoice directory")


def _open_existing_estimate(
    page: Page,
    *,
    base_url: str,
    quick_access_url: str,
    estimate_id: str,
    username: str,
    password: str,
    company: str,
) -> None:
    """Navigate to the quick-access page, use the search box to find and
    open the existing estimate, then land on the Estimate Summary tab."""
    _debug(f"Opening quick access to search for existing estimate_id={estimate_id}")
    _navigate_with_recovery(
        page,
        quick_access_url,
        base_url=base_url,
        username=username,
        password=password,
        company=company,
        step="open_existing_estimate_quick_access",
    )

    selection_page = EstimateSelectionPage(page)
    selection_page.search_and_open_estimate(estimate_id)
    _debug(f"Existing estimate {estimate_id} opened. URL: {page.url}")

    # Navigate directly to Estimate Summary tab and remove all existing items
    summary_tab = EstimatedSummaryTab(page)
    summary_tab.switch_to_tab()
    _debug(f"Switched to Estimate Summary. Removing all existing items")
    summary_tab.remove_all_items()
    _debug(f"All items removed from estimate_id={estimate_id}")


def run_estimate_flow(
    tenant_credentials: Optional[Dict[str, Any]] = None,
    quote_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tenant_credentials = tenant_credentials or {}
    username = str(tenant_credentials.get("username") or "").strip()
    password = str(tenant_credentials.get("password") or "").strip()
    company = str(tenant_credentials.get("company") or "").strip()
    base_url = str(tenant_credentials.get("printsmith_url") or "").strip()
    quick_access_url = _build_quick_access_url(base_url)
    quote_record = quote_record or {}
    estimate_id = str(quote_record.get("estimate_id") or "").strip()
    use_existing_estimate = bool(estimate_id)

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

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    invoice_path: Optional[Path] = None
    flow_failed = False
    current_step = "starting"
    started_at = time.monotonic()
    logout_succeeded = False
    logout_error: Optional[str] = None
    customer_selection_status: Optional[Dict[str, Any]] = None

    try:
        with sync_playwright() as playwright:
            csv_logger.init()
            _debug("CSV logger initialized (terminal-only)")
            _debug("Starting estimate flow")
            if quote_record:
                _debug(
                    f"Using quote context id={quote_record.get('_id') or quote_record.get('id') or quote_record.get('quote_id')}"
                    f" estimate_id={estimate_id or 'none'}"
                )

            current_step = "login"
            _ensure_within_timeout(started_at, current_step)
            browser, context, page = _ensure_browser_and_login(
                playwright,
                base_url=base_url,
                username=username,
                password=password,
                company=company,
            )

            if use_existing_estimate:
                # --- EXISTING ESTIMATE FLOW ---
                # Search for the estimate, open it, remove all existing jobs
                current_step = "open_existing_estimate"
                _ensure_within_timeout(started_at, current_step)
                _open_existing_estimate(
                    page,
                    base_url=base_url,
                    quick_access_url=quick_access_url,
                    estimate_id=estimate_id,
                    username=username,
                    password=password,
                    company=company,
                )
                _debug(f"Existing estimate opened and cleared. URL: {page.url}")

                # customer_selection_status stays None for existing estimates
                # (no customer setup needed — estimate already has a customer)
                customer_selection_status = {}

                current_step = "invoice_tabs"
                invoice_page = InvoicePage(page)
                for attempt in range(2):
                    try:
                        _ensure_within_timeout(started_at, f"{current_step}_attempt_{attempt + 1}")
                        invoice_path, estimate_totals = invoice_page.complete_information_tabs(
                            resume_from="estimate_summary",
                            quote_record=quote_record,
                            customer_selection_status=customer_selection_status,
                        )
                        break
                    except InvalidStockSearchError:
                        raise
                    except Exception:
                        logger.exception(
                            "Step failed: %s attempt %s/2", current_step, attempt + 1
                        )
                        if attempt == 0:
                            _debug("Invoice tabs (existing estimate) failed on first attempt; retrying once")
                            continue
                        raise

            else:
                # --- NEW ESTIMATE FLOW ---
                current_step = "quick_access"
                _ensure_within_timeout(started_at, current_step)
                _navigate_with_recovery(
                    page,
                    quick_access_url,
                    base_url=base_url,
                    username=username,
                    password=password,
                    company=company,
                    step=current_step,
                )
                _debug(f"Quick access page loaded. URL: {page.url}")

                current_step = "create_estimate_click"
                _ensure_within_timeout(started_at, current_step)
                estimate_page = EstimatePage(page)
                estimate_page.click_create_estimate_quick_access()
                _debug(f"Create Estimate clicked. URL: {page.url}")

                current_step = "new_estimate_setup"
                new_estimate_page = NewEstimatePage(page)
                for attempt in range(2):
                    try:
                        _ensure_within_timeout(started_at, f"{current_step}_attempt_{attempt + 1}")
                        customer_selection_status = new_estimate_page.complete_walk_in_digital_color(
                            quote_record or {}
                        )
                        break
                    except Exception:
                        logger.exception(
                            "Step failed: %s attempt %s/2", current_step, attempt + 1
                        )
                        if attempt == 0:
                            _debug("New Estimate setup failed on first attempt; retrying once")
                            continue
                        raise
                _debug(f"New Estimate setup completed. URL: {page.url}")

                current_step = "invoice_tabs"
                invoice_page = InvoicePage(page)
                for attempt in range(2):
                    try:
                        _ensure_within_timeout(started_at, f"{current_step}_attempt_{attempt + 1}")
                        invoice_path, estimate_totals = invoice_page.complete_information_tabs(
                            resume_from="auto",
                            quote_record=quote_record,
                            customer_selection_status=customer_selection_status,
                        )
                        break
                    except InvalidStockSearchError:
                        raise
                    except Exception:
                        logger.exception(
                            "Step failed: %s attempt %s/2", current_step, attempt + 1
                        )
                        if attempt == 0:
                            _debug("Invoice tabs flow failed on first attempt; retrying once")
                            continue
                        raise
            _debug(f"Invoice tab flow completed. URL: {page.url}")
            _debug(f"Estimate totals collected: {estimate_totals}")
            logger.info("Estimate totals: %s", estimate_totals)

            current_step = "save_summary"
            _ensure_within_timeout(started_at, current_step)
            upload_result = _upload_summary_file(invoice_path, quote_record)

            current_step = "logout"
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)

            return {
                "status": "success",
                "message": "Create Estimate flow completed",
                "step": current_step,
                "current_url": page.url,
                "invoice_file": str(invoice_path),
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
                "session_reused": False,
                "browser_open": False,
                "customer_selection": customer_selection_status,
                "estimate_totals": estimate_totals,
                **upload_result,
            }

    except InvalidLoginCredentialsError as exc:
        flow_failed = True
        logger.warning("Estimate flow stopped due to invalid login credentials")
        return {
            "status": "error",
            "message": str(exc),
            "step": current_step,
            "logout_succeeded": False,
            "logout_error": None,
            "customer_selection": customer_selection_status,
        }

    except EstimateLockedError as exc:
        flow_failed = True
        if page is not None:
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)
        logger.warning("Estimate flow stopped because the estimate is locked")

        return {
            "status": "error",
            "message": str(exc),
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
            "customer_selection": customer_selection_status,
        }

    except PlaywrightTimeoutError as exc:
        flow_failed = True
        if page is not None:
            try:
                _recover_session_from_home(
                    page,
                    base_url=base_url,
                    username=username,
                    password=password,
                    company=company,
                    failed_step=f"{current_step}_before_logout",
                )
            except Exception as recovery_exc:
                logger.warning(
                    "Home/login recovery before logout failed: %s",
                    recovery_exc,
                )
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)
        logger.exception("Estimate flow failed with Playwright timeout error")

        return {
            "status": "error",
            "message": str(exc),
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
            "customer_selection": customer_selection_status,
        }

    except Exception as exc:
        flow_failed = True
        if page is not None:
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)
        logger.exception("Estimate flow failed")

        return {
            "status": "error",
            "message": f"Unexpected error: {exc}",
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
            "customer_selection": customer_selection_status,
        }

    finally:
        _cleanup_local_invoice_file(invoice_path)
        _cleanup_browser(
            browser, context, page,
            flow_failed=flow_failed,
            logout_succeeded=logout_succeeded,
            logout_error=logout_error,
        )
        # Break local references so GC can reclaim objects immediately
        del browser, context, page, invoice_path
        del customer_selection_status
