import logging
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import urlparse

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
from app.v1.modules.bot.driver import create_browser_page
from app.v1.modules.bot.pages.estimate_page import EstimatePage
from app.v1.modules.bot.pages.invoice_page.invoice_page import InvoicePage
from app.v1.modules.bot.pages.invoice_page.job_details import InvalidStockSearchError
from app.v1.modules.bot.pages.login_page import InvalidLoginCredentialsError, LoginPage
from app.v1.modules.bot.pages.logout_page import LogoutPage
from app.v1.modules.bot.pages.new_estimate_page import NewEstimatePage

logger = logging.getLogger(__name__)
FLOW_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS

_flow_lock = Lock()


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][Service] {message}")
    logger.info(message)


def _build_quick_access_url(base_url: str) -> str:
    if "/PrintSmith/PrintSmith.html" in base_url:
        return base_url.replace(
            "/PrintSmith/PrintSmith.html",
            "/PrintSmith/nextgen/en_US/#/quick-access",
        )

    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/PrintSmith/nextgen/en_US/#/quick-access"

    return base_url


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


def _login(
    page: Page,
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
) -> None:
    _debug(f"Opening login page: {base_url}")
    page.goto(base_url)

    login_page = LoginPage(page)
    login_page.login(username, password, company)
    login_page.wait_for_login_result()
    BasePage(page).wait_for_spinner_to_disappear()
    _debug(f"Login successful. URL: {page.url}")


def _ensure_browser_and_login(
    playwright,
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
) -> tuple[Browser, BrowserContext, Page]:
    _debug("Creating fresh Playwright browser and logging in for this request.")
    browser, context, page = create_browser_page(playwright)
    _login(
        page,
        base_url=base_url,
        username=username,
        password=password,
        company=company,
    )
    return browser, context, page


def _logout_if_possible(
    page: Optional[Page], retries: int = 1
) -> tuple[bool, Optional[str]]:
    if page is None:
        return False, "page_not_available"
    last_error: Optional[str] = None
    for attempt in range(1, retries + 2):
        try:
            logout_page = LogoutPage(page)
            logout_page.logout()
            _debug("Logout flow completed")
            return True, None
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Logout attempt %s failed: %s", attempt, exc)
            if attempt <= retries:
                time.sleep(0.5)
    return False, last_error


def _ensure_within_timeout(started_at: float, step: str) -> None:
    elapsed = time.monotonic() - started_at
    if elapsed > FLOW_TIMEOUT_SECONDS:
        raise PlaywrightTimeoutError(
            f"PSV bot flow timeout after {int(elapsed)}s at step '{step}'"
        )


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
    page: Optional[Page] = None
    invoice_path: Optional[Path] = None
    flow_failed = False
    current_step = "starting"
    started_at = time.monotonic()
    logout_succeeded = False
    logout_error: Optional[str] = None
    customer_selection_status: Optional[Dict[str, Any]] = None

    try:
        with _flow_lock:
            with sync_playwright() as playwright:
                log_path = csv_logger.init()
                _debug(f"CSV log started: {log_path}")
                _debug("Starting estimate flow")
                if quote_record:
                    _debug(
                        f"Using quote context id={quote_record.get('_id') or quote_record.get('id') or quote_record.get('quote_id')}"
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

                current_step = "quick_access"
                _ensure_within_timeout(started_at, current_step)
                page.goto(quick_access_url)
                BasePage(page).wait_for_spinner_to_disappear()
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
                        invoice_path = invoice_page.complete_information_tabs(
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

    except PlaywrightTimeoutError as exc:
        flow_failed = True
        if page is not None:
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
        csv_logger.shutdown()
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
            logger.info(
                "Browser closed (flow_failed=%s, logout_succeeded=%s, logout_error=%s)",
                flow_failed,
                logout_succeeded,
                logout_error,
            )
