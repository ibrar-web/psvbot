import logging
from typing import Any, Callable, Dict, List, Tuple

from playwright.sync_api import Page, sync_playwright

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.pages.login_page import InvalidLoginCredentialsError
from app.v1.modules.bot.session_runner import (
    _cleanup_browser,
    _ensure_browser_and_login,
    _logout_if_possible,
)

logger = logging.getLogger(__name__)

# (rows fetched, rows that failed with a reason)
FetchResult = Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]
FetchStep = Callable[[Page, Dict[str, Any]], FetchResult]


def run_pricing_flow(
    flow_name: str,
    fetch_step: FetchStep,
    tenant_credentials: Dict[str, Any],
    task_payload: Dict[str, Any],
) -> Dict[str, Any]:
    browser = context = page = None
    logout_succeeded, logout_error = False, None

    with sync_playwright() as playwright:
        try:
            logger.info("Starting %s flow", flow_name)
            browser, context, page = _ensure_browser_and_login(
                playwright,
                base_url=tenant_credentials["printsmith_url"],
                username=tenant_credentials["username"],
                password=tenant_credentials["password"],
                company=tenant_credentials["company"],
            )
            BasePage(page).wait_for_spinner_to_disappear()
            rows, failed = fetch_step(page, task_payload)
            logout_succeeded, logout_error = _logout_if_possible(page)
            return {
                "status": "success",
                "message": (
                    f"{flow_name} completed with {len(failed)} failed rows"
                    if failed
                    else f"{flow_name} completed"
                ),
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
                "data": rows,
                "total_rows": len(rows),
                "failed": failed,
                "failed_rows": len(failed),
            }
        except InvalidLoginCredentialsError as exc:
            logger.warning("%s stopped: invalid login credentials", flow_name)
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.exception("%s failed", flow_name)
            if page is not None:
                logout_succeeded, logout_error = _logout_if_possible(page)
            return {
                "status": "error",
                "message": str(exc),
                "logout_succeeded": logout_succeeded,
                "logout_error": logout_error,
            }
        finally:
            _cleanup_browser(
                browser,
                context,
                page,
                logout_succeeded=logout_succeeded,
                logout_error=logout_error,
            )
