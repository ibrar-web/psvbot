import logging
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.remote.webdriver import WebDriver

from app.v1.common.storage_service import build_s3_key, generate_presigned_download_url, upload_bytes_to_s3
from app.v1.core.settings import BUCKET_NAME, QUOTE_SUMMARY_STORAGE_ROOT
from app.v1.modules.bot.config import DEBUG, DEFAULT_TIMEOUT_SECONDS
from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.driver import create_driver
from app.v1.modules.bot.pages.estimate_page import EstimatePage
from app.v1.modules.bot.pages.invoice_page.invoice_page import InvoicePage
from app.v1.modules.bot.pages.login_page import LoginPage
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
    tenant_id = str((quote_record or {}).get("tenant_id") or "adhoc").strip() or "adhoc"
    quote_id = str(
        (quote_record or {}).get("_id")
        or (quote_record or {}).get("id")
        or (quote_record or {}).get("quote_id")
        or "manual"
    ).strip() or "manual"
    target_dir = root / tenant_id / quote_id
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / summary_file_name


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

    folder_prefix = f"{QUOTE_SUMMARY_STORAGE_ROOT}/{tenant_id}/quotations/{quote_id}"
    storage_key = build_s3_key(folder_prefix, summary_file_name)
    upload_bytes_to_s3(
        key=storage_key,
        content=invoice_path.read_bytes(),
        content_type="application/pdf",
        metadata={
            "tenant_id": tenant_id,
            "quote_id": quote_id,
        },
    )

    return {
        "summary_file_name": summary_file_name,
        "summary_file_path": str(saved_summary_path),
        "summary_file_storage_key": storage_key,
        "summary_file_gcs_uri": f"gs://{BUCKET_NAME}/{storage_key}",
        "summary_file_url": generate_presigned_download_url(key=storage_key),
    }


def _login(
    driver: WebDriver,
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
) -> None:
    _debug(f"Opening login page: {base_url}")
    driver.get(base_url)

    login_page = LoginPage(driver)
    login_page.login(username, password, company)
    login_page.wait_for_login_result()
    BasePage(driver).wait_for_spinner_to_disappear()
    _debug(f"Login successful. URL: {driver.current_url}")


def _ensure_driver_and_login(
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
) -> WebDriver:
    _debug("Creating fresh driver and logging in for this request.")
    driver = create_driver()
    _login(
        driver,
        base_url=base_url,
        username=username,
        password=password,
        company=company,
    )
    return driver


def _logout_if_possible(
    driver: Optional[WebDriver], retries: int = 1
) -> tuple[bool, Optional[str]]:
    if driver is None:
        return False, "driver_not_available"
    last_error: Optional[str] = None
    for attempt in range(1, retries + 2):
        try:
            logout_page = LogoutPage(driver)
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
        raise TimeoutException(
            f"PSV bot flow timeout after {int(elapsed)}s at step '{step}'"
        )


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

    driver: Optional[WebDriver] = None
    invoice_path: Optional[Path] = None
    flow_failed = False
    current_step = "starting"
    started_at = time.monotonic()
    logout_succeeded = False
    logout_error: Optional[str] = None

    try:
        with _flow_lock:
            _debug("Starting estimate flow")
            if quote_record:
                _debug(
                    f"Using quote context id={quote_record.get('_id') or quote_record.get('id') or quote_record.get('quote_id')}"
                )
            current_step = "login"
            _ensure_within_timeout(started_at, current_step)
            driver = _ensure_driver_and_login(
                base_url=base_url,
                username=username,
                password=password,
                company=company,
            )

            current_step = "quick_access"
            _ensure_within_timeout(started_at, current_step)
            driver.get(quick_access_url)
            BasePage(driver).wait_for_spinner_to_disappear()
            _debug(f"Quick access page loaded. URL: {driver.current_url}")

            current_step = "create_estimate_click"
            _ensure_within_timeout(started_at, current_step)
            estimate_page = EstimatePage(driver)
            estimate_page.click_create_estimate_quick_access()
            _debug(f"Create Estimate clicked. URL: {driver.current_url}")

            current_step = "new_estimate_setup"
            new_estimate_page = NewEstimatePage(driver)
            for attempt in range(2):
                try:
                    _ensure_within_timeout(started_at, f"{current_step}_attempt_{attempt + 1}")
                    new_estimate_page.complete_walk_in_digital_color(quote_record or {})
                    break
                except Exception:
                    logger.exception(
                        "Step failed: %s attempt %s/2", current_step, attempt + 1
                    )
                    if attempt == 0:
                        _debug("New Estimate setup failed on first attempt; retrying once")
                        continue
                    raise
            _debug(f"New Estimate setup completed. URL: {driver.current_url}")

            current_step = "invoice_tabs"
            invoice_page = InvoicePage(driver)
            for attempt in range(2):
                try:
                    _ensure_within_timeout(started_at, f"{current_step}_attempt_{attempt + 1}")
                    invoice_path = invoice_page.complete_information_tabs(
                        resume_from="auto",
                        quote_record=quote_record,
                    )
                    break
                except Exception:
                    logger.exception(
                        "Step failed: %s attempt %s/2", current_step, attempt + 1
                    )
                    if attempt == 0:
                        _debug("Invoice tabs flow failed on first attempt; retrying once")
                        continue
                    raise
            _debug(f"Invoice tab flow completed. URL: {driver.current_url}")

            current_step = "save_summary"
            _ensure_within_timeout(started_at, current_step)
            upload_result = _upload_summary_file(invoice_path, quote_record)

            current_step = "logout"
            logout_succeeded, logout_error = _logout_if_possible(driver, retries=1)

            return {
                "status": "success",
                "message": "Create Estimate flow completed",
                "step": current_step,
                "current_url": driver.current_url,
                "invoice_file": str(invoice_path),
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
                "session_reused": False,
                "browser_open": False,
                **upload_result,
            }

    except (TimeoutException, WebDriverException) as exc:
        flow_failed = True
        logout_succeeded, logout_error = _logout_if_possible(driver, retries=1)
        logger.exception("Estimate flow failed with Selenium error")

        return {
            "status": "error",
            "message": str(exc),
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
        }

    except Exception as exc:
        flow_failed = True
        logout_succeeded, logout_error = _logout_if_possible(driver, retries=1)
        logger.exception("Estimate flow failed")

        return {
            "status": "error",
            "message": f"Unexpected error: {exc}",
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
        }

    finally:
        if invoice_path is not None:
            try:
                invoice_path.unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to delete temporary invoice file")
        if driver is not None:
            if not logout_succeeded:
                logout_succeeded, logout_error = _logout_if_possible(driver, retries=1)
            try:
                driver.quit()
            finally:
                pass
            logger.info(
                "Driver closed (flow_failed=%s, logout_succeeded=%s, logout_error=%s)",
                flow_failed,
                logout_succeeded,
                logout_error,
            )
