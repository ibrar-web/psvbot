import logging
import time
from pathlib import Path
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
from app.v1.modules.bot.etimate_history.pages.estimate_history_export_page import (
    EstimateHistoryExportPage,
)

logger = logging.getLogger(__name__)


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][EstimateHistoryExport] {message}")
    logger.info(message)


def run_estimate_history_export_flow(
    tenant_credentials: Optional[Dict[str, Any]] = None,
    task_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tenant_credentials = tenant_credentials or {}
    task_payload = task_payload or {}
    username = str(tenant_credentials.get("username") or "").strip()
    password = str(tenant_credentials.get("password") or "").strip()
    company = str(tenant_credentials.get("company") or "").strip()
    base_url = str(tenant_credentials.get("printsmith_url") or "").strip()

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
    csv_path: Optional[Path] = None
    flow_failed = False
    current_step = "starting"
    started_at = time.monotonic()
    logout_succeeded = False
    logout_error: Optional[str] = None

    try:
        with sync_playwright() as playwright:
            csv_logger.init()
            _debug("Starting estimate history export flow")

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
            history_page = EstimateHistoryExportPage(page)
            history_page.open_from_quick_access()
            _debug(f"Estimate History grid opened. URL: {page.url}")

            current_step = "clear_filters"
            _ensure_within_timeout(started_at, current_step)
            history_page.clear_filters()

            current_step = "download_csv"
            _ensure_within_timeout(started_at, current_step)
            csv_path = history_page.download_csv()
            _debug(f"CSV downloaded to: {csv_path}")

            current_step = "logout"
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)

            return {
                "status": "success",
                "message": "Estimate history export completed",
                "step": current_step,
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
                # Local temp file only — no public copy is made any more.
                # The caller (queue_service._call_record_result) reads and
                # uploads these actual bytes as a multipart file attachment,
                # then deletes the temp file once the callback completes.
                "history_file_name": csv_path.name,
                "history_file_local_path": str(csv_path),
            }

    except InvalidLoginCredentialsError as exc:
        flow_failed = True
        logger.warning("Estimate history export stopped due to invalid login credentials")
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
        logger.exception("Estimate history export failed with Playwright timeout error")
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
        logger.exception("Estimate history export failed")
        return {
            "status": "error",
            "message": f"Unexpected error: {exc}",
            "step": current_step,
            "logout_succeeded": logout_succeeded,
            "logout_error": logout_error,
        }

    finally:
        # The temp CSV (if any) is intentionally NOT deleted here — it must
        # still exist when queue_service._call_record_result uploads it as
        # the result callback's file attachment. That call happens after
        # this flow returns, and is responsible for deleting it afterward.
        _cleanup_browser(
            browser, context, page,
            flow_failed=flow_failed,
            logout_succeeded=logout_succeeded,
            logout_error=logout_error,
        )
        del browser, context, page, csv_path
