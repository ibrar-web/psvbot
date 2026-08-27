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
from app.v1.modules.bot.etimate_history.pages.estimate_history_lookup_page import (
    EstimateHistoryLookupPage,
)

logger = logging.getLogger(__name__)


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][EstimateHistoryLookup] {message}")
    logger.info(message)


def run_estimate_history_lookup_flow(
    tenant_credentials: Optional[Dict[str, Any]] = None,
    task_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search the Estimate History grid for one estimate_id and open it.

    Scope for now: login -> open Estimate History -> filter by Estimate # ->
    click the matching row. Does not yet read/scrape the opened record's
    detail fields — that's a follow-up once we've seen what that screen
    actually looks like live.
    """
    tenant_credentials = tenant_credentials or {}
    task_payload = task_payload or {}
    username = str(tenant_credentials.get("username") or "").strip()
    password = str(tenant_credentials.get("password") or "").strip()
    company = str(tenant_credentials.get("company") or "").strip()
    base_url = str(tenant_credentials.get("printsmith_url") or "").strip()
    estimate_id = str(task_payload.get("estimate_id") or "").strip()

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
    if not estimate_id:
        return {
            "status": "error",
            "message": "Missing estimate_id for history lookup",
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
            _debug(f"Starting estimate history lookup flow for estimate_id={estimate_id}")

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
            history_page = EstimateHistoryLookupPage(page)
            history_page.open_from_quick_access()
            _debug(f"Estimate History grid opened. URL: {page.url}")

            current_step = "search_and_open_record"
            _ensure_within_timeout(started_at, current_step)
            history_page.search_and_open_by_estimate_id(estimate_id)

            current_step = "logout"
            logout_succeeded, logout_error = _logout_if_possible(page, retries=1)

            return {
                "status": "success",
                "message": "Estimate history record opened",
                "step": current_step,
                "estimate_id": estimate_id,
                "current_url": page.url,
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
            }

    except InvalidLoginCredentialsError as exc:
        flow_failed = True
        logger.warning("Estimate history lookup stopped due to invalid login credentials")
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
        logger.exception("Estimate history lookup failed with Playwright timeout error")
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
        logger.exception("Estimate history lookup failed")
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
