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

# (rows fetched, rows that failed with a reason, extra result fields)
FetchResult = Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]
FetchStep = Callable[[Page, Dict[str, Any]], FetchResult]

# Refresh tasks send the labels the server already stores; only PSV items
# whose label isn't among them get their (slow) details request.
KNOWN_LABELS_KEY = "known_labels"


def normalize_label(label: Any) -> str:
    return " ".join(str(label or "").split()).casefold()


def split_known(
    items: Dict[Any, Any],
    label_of: Callable[[Any], str],
    task_payload: Dict[str, Any],
) -> Tuple[Dict[Any, Any], Dict[str, Any]]:
    """Return (items not in known_labels, extra refresh result fields)."""
    known_labels = [
        label
        for label in task_payload.get(KNOWN_LABELS_KEY) or []
        if normalize_label(label)
    ]
    known = {normalize_label(label) for label in known_labels}
    psv_labels = {normalize_label(label_of(item)) for item in items.values()}
    new_items = {
        key: item
        for key, item in items.items()
        if normalize_label(label_of(item)) not in known
    }
    # Labels the server stores that PSV no longer has (deleted or renamed).
    removed_labels = [
        label
        for label in known_labels
        if normalize_label(label) not in psv_labels
    ]
    logger.info(
        "Refresh: %s in PSV, %s known labels, %s new, %s removed",
        len(items),
        len(known),
        len(new_items),
        len(removed_labels),
    )
    return new_items, {
        "known_rows": len(items) - len(new_items),
        "removed_labels": removed_labels,
    }


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
            rows, failed, extra = fetch_step(page, task_payload)
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
                **extra,
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
