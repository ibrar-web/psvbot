import gc
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.config import (
    DEBUG,
    DEFAULT_TIMEOUT_SECONDS,
    PAGE_LOAD_TIMEOUT_SECONDS,
    RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
)
from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.driver import create_browser_page
from app.v1.modules.bot.pages.login_page import LoginPage
from app.v1.modules.bot.pages.logout_page import LogoutPage

logger = logging.getLogger(__name__)
FLOW_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[PrintSmith][Session] {message}")
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


def _is_logged_in_url(url: str) -> bool:
    normalized_url = (url or "").lower()
    return any(
        part in normalized_url
        for part in ("nextgen", "quick-access", "#/home", "/home")
    )


def _safe_page_url(page: Page) -> str:
    try:
        return page.url
    except Exception:
        return "unavailable"


def _stop_page_load(page: Page) -> None:
    client = None
    try:
        client = page.context.new_cdp_session(page)
        client.send("Page.stopLoading")
    except Exception:
        logger.debug("Unable to stop current page load before recovery", exc_info=True)
    finally:
        if client is not None:
            try:
                client.detach()
            except Exception:
                pass


def _wait_for_app_to_settle(page: Page, *, timeout_seconds: float, step: str) -> None:
    try:
        BasePage(page, timeout=timeout_seconds).wait_for_spinner_to_disappear()
    except PlaywrightTimeoutError as exc:
        raise PlaywrightTimeoutError(
            f"Timed out after {timeout_seconds:.1f}s waiting for PSV page to settle "
            f"at step '{step}'. Current URL: {_safe_page_url(page)}"
        ) from exc


def _load_page(
    page: Page,
    url: str,
    *,
    step: str,
    timeout_seconds: int,
) -> None:
    _debug(f"Opening {step}: {url} (timeout={timeout_seconds}s)")
    deadline = time.monotonic() + timeout_seconds
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=timeout_seconds * 1000,
    )
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        raise PlaywrightTimeoutError(
            f"Timed out after {timeout_seconds}s loading PSV page at step '{step}' "
            f"before the page spinner settled. Current URL: {_safe_page_url(page)}"
        )
    _wait_for_app_to_settle(page, timeout_seconds=remaining_seconds, step=step)
    _debug(f"{step} loaded. URL: {_safe_page_url(page)}")


def _complete_login_if_needed(
    page: Page,
    *,
    username: str,
    password: str,
    company: str,
    timeout_seconds: int,
    step: str,
) -> None:
    login_page = LoginPage(page, timeout=timeout_seconds)
    if _is_logged_in_url(page.url) and not login_page.is_visible(
        LoginPage.USERNAME_INPUT
    ):
        _debug(f"{step}: user is already logged in. URL: {page.url}")
        _wait_for_app_to_settle(page, timeout_seconds=timeout_seconds, step=step)
        return

    try:
        login_page.wait_for_visible(LoginPage.USERNAME_INPUT)
    except PlaywrightTimeoutError as exc:
        if _is_logged_in_url(page.url):
            _debug(f"{step}: login form not visible because user is logged in")
            _wait_for_app_to_settle(page, timeout_seconds=timeout_seconds, step=step)
            return
        raise PlaywrightTimeoutError(
            f"{step}: login form did not appear within {timeout_seconds}s. "
            f"Current URL: {_safe_page_url(page)}"
        ) from exc

    login_page.login(username, password, company)
    login_page.wait_for_login_result()
    _wait_for_app_to_settle(page, timeout_seconds=timeout_seconds, step=step)
    _debug(f"{step}: login successful. URL: {page.url}")


def _recover_session_from_home(
    page: Page,
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
    failed_step: str,
) -> None:
    _debug(
        f"{failed_step} did not load within {PAGE_LOAD_TIMEOUT_SECONDS}s; "
        "opening home/login page for recovery"
    )
    _stop_page_load(page)
    _load_page(
        page,
        base_url,
        step=f"{failed_step}_recovery_home",
        timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
    )
    _complete_login_if_needed(
        page,
        username=username,
        password=password,
        company=company,
        timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
        step=f"{failed_step}_recovery_login",
    )


def _navigate_with_recovery(
    page: Page,
    url: str,
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
    step: str,
) -> None:
    try:
        _load_page(
            page,
            url,
            step=step,
            timeout_seconds=PAGE_LOAD_TIMEOUT_SECONDS,
        )
        return
    except PlaywrightTimeoutError as first_exc:
        logger.warning(
            "Page load timeout at step=%s url=%s; attempting home/login recovery",
            step,
            url,
        )
        _recover_session_from_home(
            page,
            base_url=base_url,
            username=username,
            password=password,
            company=company,
            failed_step=step,
        )
        try:
            _load_page(
                page,
                url,
                step=f"{step}_retry_after_recovery",
                timeout_seconds=PAGE_LOAD_TIMEOUT_SECONDS,
            )
            return
        except PlaywrightTimeoutError as second_exc:
            raise PlaywrightTimeoutError(
                f"{step} failed to load within {PAGE_LOAD_TIMEOUT_SECONDS}s, "
                "even after home/login recovery. "
                f"Original error: {first_exc}; retry error: {second_exc}"
            ) from second_exc


def _login(
    page: Page,
    *,
    base_url: str,
    username: str,
    password: str,
    company: str,
) -> None:
    try:
        _load_page(
            page,
            base_url,
            step="login_page",
            timeout_seconds=PAGE_LOAD_TIMEOUT_SECONDS,
        )
    except PlaywrightTimeoutError:
        _debug(
            "Login page did not load within page timeout; "
            "retrying with recovery home timeout"
        )
        _stop_page_load(page)
        _load_page(
            page,
            base_url,
            step="login_page_recovery",
            timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
        )

    _complete_login_if_needed(
        page,
        username=username,
        password=password,
        company=company,
        timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
        step="login",
    )


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
    page: Optional[Page],
    retries: int = 1,
    timeout_seconds: int = RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
) -> tuple[bool, Optional[str]]:
    if page is None:
        return False, "page_not_available"
    last_error: Optional[str] = None
    for attempt in range(1, retries + 2):
        try:
            logout_page = LogoutPage(page, timeout=timeout_seconds)
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


def _cleanup_browser(
    browser: Optional[Browser],
    context: Optional[BrowserContext],
    page: Optional[Page],
    *,
    flow_failed: bool = False,
    logout_succeeded: bool = False,
    logout_error: Optional[str] = None,
) -> None:
    """Thoroughly tear down Playwright resources and release memory."""
    # 1. Stop any injected JS observers while page is still alive
    if page is not None:
        try:
            BasePage(page).stop_warning_auto_dismiss()
        except Exception:
            pass

    # 2. Close in correct order: page -> context -> browser
    if page is not None:
        try:
            page.close()
        except Exception:
            pass
    if context is not None:
        try:
            context.close()
        except Exception:
            pass
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

    # 3. Force garbage collection to reclaim Chromium process memory
    gc.collect()
