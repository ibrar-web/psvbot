"""
Manual flow tester — mirrors run_estimate_flow() exactly but skips the storage upload.

Usage:
    python test_pages.py

Set credentials in .env or as environment variables:
    PRINTSMITH_URL
    PRINTSMITH_USERNAME
    PRINTSMITH_PASSWORD
    PRINTSMITH_COMPANY      (optional — derived from URL hostname if omitted)
    PRINTSMITH_HEADLESS     (default: false for local testing)
    PRINTSMITH_DEBUG        (default: true)
    PRINTSMITH_TIMEOUT_SECONDS (default: 120)
"""

import gc
import json
import os
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

load_dotenv()

# ---------------------------------------------------------------------------
# Test payload — same shape the queue service receives from the main server.
# tenant_credentials takes priority over the top-level .env credentials.
# Set estimate_id to a non-empty string to exercise the existing-estimate branch.
# ---------------------------------------------------------------------------
QUOTE_RECORD = {
   "queue_id": "6a0f0c591d8b5bc19f59d90c",
   "BACK_URL_STATUS_UPDATE": "http://localhost:8000/api/v1/quotation/job/6a0f0c591d8b5bc19f59d90c/quote-detail",
   "BACK_URL_RECORD_RESULT": "http://localhost:8000/api/v1/quotation/job/result",
   "quote": {
      "_id": "6a0f0c591d8b5bc19f59d90b",
      "tenant_id": "6a0ac09549e818e8e4b8b447",
      "user_email": "qatestingadmin@yopmail.com",
      "company_name": "blacklion",
      "street": "",
      "city": "",
      "account_name": "QATestingAdmin",
      "contact_person": "QATestingAdmin",
      "contact_email": "qatestingadmin@yopmail.com",
      "contact_phone": "",
      "customer_name": None,
      "chat_id": "fdbc0b53-7abe-4dc3-8b42-105875b3356b",
      "message_id": "bb61eda2-b1fa-4954-957f-6d51cb5d97ce",
   },
   "requirements": [
      {
         "stock_search": "Kelly Dig Silk Cvr 100 83M 18 X12 410/cs",
         "quantity": 5.0,
         "size": "3x4",
         "sides": "duplex",
         "description": "50 double-sided 3x4 laminated badges with one hole at the top for a lanyard and round corners",
         "job_method": "Sublet",
         "job_charges": [
            {"charge_name": "1 Hole Punch (Laminated)", "charge_price": 0.1,  "quantity": 50.0},
            {"charge_name": "Lanyards",                 "charge_price": 1.5,  "quantity": 50.0},
            {"charge_name": "Laminate 12 x 18",         "charge_price": 8.0,  "quantity": 50.0},
            {"charge_name": "Round 4 Corners: Laminated","charge_price": 0.6,  "quantity": 50.0},
         ],
         "other_charges": [],
         "rush_fee": 0.0,
         "total": 786.5,
         "estimate_totals": {},
         "vendor_name" : "Print 2 Fly",
      },
      {
         "stock_search": "][ PVC 3mm Black",
         "quantity": 75.0,
         "size": "3x4",
         "sides": "duplex",
         "description": "75 double-sided 3x4 PVC name tags with one hole at the top for a lanyard, lamination, and round corners",
         "job_method": "Large Format",
         "job_charges": [
            {"charge_name": "1 Hole Punch",                                          "charge_price": 0.03, "quantity": 75.0},
            {"charge_name": "Lanyards",                                              "charge_price": 1.5,  "quantity": 75.0},
            {"charge_name": "Laminate 12 x 18",                                     "charge_price": 8.0,  "quantity": 75.0},
            {"charge_name": "Round 4 Corners: Laminated",                           "charge_price": 0.6,  "quantity": 75.0},
            {"charge_name": "Routing Knife 4 strt sides-Foam/Coroplast/PVC/Styrene","charge_price": 6.0,  "quantity": 1.0},
         ],
         "other_charges": [],
         "rush_fee": 0.0,
         "total": 1042.25,
         "estimate_totals": {},
         "vendor_name" : None,
      },
   ],
   "tenant_credentials": {
      "printsmith_username": "AijazAsif",
      "printsmith_password": "Howard@530SF",
      "printsmith_url": "https://alphagraphics685.myprintdesk.net/PrintSmith/PrintSmith.html",
      "printsmith_company": "alphagraphics685",
   },
   "estimate_id": "38090",
}

_flow_lock = Lock()


# ---------------------------------------------------------------------------
# Credential helpers — mirrors queue_service._normalize_runtime_credentials
# ---------------------------------------------------------------------------

def _normalize_runtime_credentials(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    payload = data or {}
    printsmith_url = str(
        payload.get("printsmith_url") or payload.get("url") or os.getenv("PRINTSMITH_URL", "")
    ).strip()
    username = str(
        payload.get("printsmith_username")
        or payload.get("username")
        or os.getenv("PRINTSMITH_USERNAME", "")
    ).strip()
    password = str(
        payload.get("printsmith_password")
        or payload.get("password")
        or os.getenv("PRINTSMITH_PASSWORD", "")
    ).strip()
    company = str(
        payload.get("printsmith_company")
        or payload.get("company")
        or os.getenv("PRINTSMITH_COMPANY", "")
    ).strip()

    if not company and printsmith_url:
        hostname = urlparse(printsmith_url).hostname or ""
        company = hostname.split(".", 1)[0].strip()

    return {
        "printsmith_url": printsmith_url,
        "username": username,
        "password": password,
        "company": company,
    }


def _extract_psv_credentials(payload: Dict[str, Any], quote_record: Dict[str, Any]) -> Dict[str, Any]:
    return (
        payload.get("tenant_credentials")
        or payload.get("psv_credentials")
        or payload.get("credentials")
        or {
            "printsmith_url": quote_record.get("printsmith_url", ""),
            "printsmith_username": quote_record.get("printsmith_username", ""),
            "printsmith_password": quote_record.get("printsmith_password", ""),
            "printsmith_company": quote_record.get("printsmith_company", ""),
        }
    )


def _build_bot_quote_record(payload: Dict[str, Any]) -> Dict[str, Any]:
    quote = payload.get("quote") or {}
    raw_requirements = payload.get("requirements") or []
    tenant_credentials = payload.get("tenant_credentials") or {}
    quote_id = str(
        quote.get("_id") or quote.get("id") or quote.get("quote_id") or payload.get("queue_id") or ""
    )

    if isinstance(raw_requirements, dict):
        raw_requirements = [raw_requirements]

    requirements = []
    for req in raw_requirements:
        if not isinstance(req, dict):
            continue
        requirements.append(
            {
                "stock_search": req.get("stock_search", ""),
                "quantity": req.get("quantity", ""),
                "size": req.get("size", ""),
                "sides": req.get("sides", ""),
                "description": req.get("description", ""),
                "job_method": req.get("job_method", ""),
                "job_charges": req.get("job_charges", []),
                "other_charges": req.get("other_charges", req.get("other_chrages", [])),
                "total": req.get("total", ""),
                "vendor_name": req.get("vendor_name", ""),
            }
        )

    return {
        "_id": quote_id,
        "quote_id": quote_id,
        "estimate_id": payload.get("estimate_id") or None,
        "tenant_id": quote.get("tenant_id"),
        "user_email": quote.get("user_email", ""),
        "printsmith_url": tenant_credentials.get("printsmith_url") or "",
        "printsmith_username": tenant_credentials.get("printsmith_username") or "",
        "printsmith_password": tenant_credentials.get("printsmith_password") or "",
        "printsmith_company": tenant_credentials.get("printsmith_company") or "",
        "account_name": quote.get("account_name", ""),
        "contact_person": quote.get("contact_person", ""),
        "contact_email": quote.get("contact_email", ""),
        "contact_phone": quote.get("contact_phone", ""),
        "requirements": requirements,
    }


# ---------------------------------------------------------------------------
# Main test — mirrors run_estimate_flow() step-by-step
# ---------------------------------------------------------------------------

def test_full_flow() -> bool:
    from app.v1.modules.bot import csv_logger
    from app.v1.modules.bot.base_page import BasePage
    from app.v1.modules.bot.config import (
        DEFAULT_TIMEOUT_SECONDS,
        PAGE_LOAD_TIMEOUT_SECONDS,
        RECOVERY_HOME_LOAD_TIMEOUT_SECONDS,
    )
    from app.v1.modules.bot.driver import create_browser_page
    from app.v1.modules.bot.pages.estimate_page import EstimatePage
    from app.v1.modules.bot.pages.estimate_selection_page import EstimateSelectionPage
    from app.v1.modules.bot.pages.invoice_page.invoice_page import InvoicePage
    from app.v1.modules.bot.pages.invoice_page.estimated_summary import EstimatedSummaryTab
    from app.v1.modules.bot.pages.invoice_page.job_details import InvalidStockSearchError
    from app.v1.modules.bot.pages.login_page import InvalidLoginCredentialsError, LoginPage
    from app.v1.modules.bot.pages.logout_page import LogoutPage
    from app.v1.modules.bot.pages.new_estimate_page import NewEstimatePage

    # --- Resolve credentials (same priority chain as queue_service) ---
    psv_credentials = _extract_psv_credentials(QUOTE_RECORD, {})
    runtime_credentials = _normalize_runtime_credentials(psv_credentials)
    quote_record = _build_bot_quote_record(QUOTE_RECORD)

    username  = runtime_credentials["username"]
    password  = runtime_credentials["password"]
    company   = runtime_credentials["company"]
    base_url  = runtime_credentials["printsmith_url"]
    estimate_id = str(quote_record.get("estimate_id") or "").strip()
    use_existing_estimate = bool(estimate_id)

    def _build_quick_access_url(url: str) -> str:
        if "/PrintSmith/PrintSmith.html" in url:
            return url.replace(
                "/PrintSmith/PrintSmith.html",
                "/PrintSmith/nextgen/en_US/#/quick-access",
            )
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/PrintSmith/nextgen/en_US/#/quick-access"
        return url

    quick_access_url = _build_quick_access_url(base_url)

    def _is_logged_in_url(url: str) -> bool:
        normalized = (url or "").lower()
        return any(p in normalized for p in ("nextgen", "quick-access", "#/home", "/home"))

    def _safe_url(pg) -> str:
        try:
            return pg.url
        except Exception:
            return "unavailable"

    def _stop_page_load(pg) -> None:
        try:
            pg.evaluate("() => window.stop()")
        except Exception:
            pass

    def _wait_for_settle(pg, *, timeout_seconds: int, step: str) -> None:
        try:
            BasePage(pg, timeout=timeout_seconds).wait_for_spinner_to_disappear()
        except PlaywrightTimeoutError as exc:
            raise PlaywrightTimeoutError(
                f"Timed out after {timeout_seconds}s waiting for app to settle at '{step}'. "
                f"URL: {_safe_url(pg)}"
            ) from exc

    def _load_page(pg, url: str, *, step: str, timeout_seconds: int) -> None:
        print(f"    -> Loading {step}: {url}")
        pg.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
        _wait_for_settle(pg, timeout_seconds=timeout_seconds, step=step)
        print(f"    -> {step} loaded. URL: {_safe_url(pg)}")

    def _complete_login_if_needed(pg, *, step: str, timeout_seconds: int) -> None:
        login_page = LoginPage(pg, timeout=timeout_seconds)
        if _is_logged_in_url(pg.url) and not login_page.is_visible(LoginPage.USERNAME_INPUT):
            print(f"    -> {step}: already logged in")
            _wait_for_settle(pg, timeout_seconds=timeout_seconds, step=step)
            return
        try:
            login_page.wait_for_visible(LoginPage.USERNAME_INPUT)
        except PlaywrightTimeoutError as exc:
            if _is_logged_in_url(pg.url):
                print(f"    -> {step}: login form not visible because already logged in")
                _wait_for_settle(pg, timeout_seconds=timeout_seconds, step=step)
                return
            raise PlaywrightTimeoutError(
                f"{step}: login form did not appear within {timeout_seconds}s. "
                f"URL: {_safe_url(pg)}"
            ) from exc
        login_page.login(username, password, company)
        login_page.wait_for_login_result()
        _wait_for_settle(pg, timeout_seconds=timeout_seconds, step=step)
        print(f"    -> {step}: login successful. URL: {_safe_url(pg)}")

    def _navigate_with_recovery(pg, url: str, *, step: str) -> None:
        try:
            _load_page(pg, url, step=step, timeout_seconds=PAGE_LOAD_TIMEOUT_SECONDS)
            return
        except PlaywrightTimeoutError as first_exc:
            print(f"    [WARN] {step} timed out; attempting home/login recovery")
            _stop_page_load(pg)
            _load_page(pg, base_url, step=f"{step}_recovery_home", timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS)
            _complete_login_if_needed(pg, step=f"{step}_recovery_login", timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS)
            try:
                _load_page(pg, url, step=f"{step}_retry_after_recovery", timeout_seconds=PAGE_LOAD_TIMEOUT_SECONDS)
                return
            except PlaywrightTimeoutError as second_exc:
                raise PlaywrightTimeoutError(
                    f"{step} failed even after recovery. "
                    f"Original: {first_exc}; Retry: {second_exc}"
                ) from second_exc

    def _login(pg) -> None:
        try:
            _load_page(pg, base_url, step="login_page", timeout_seconds=PAGE_LOAD_TIMEOUT_SECONDS)
        except PlaywrightTimeoutError:
            print("    [WARN] Login page timed out; retrying with recovery timeout")
            _stop_page_load(pg)
            _load_page(pg, base_url, step="login_page_recovery", timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS)
        _complete_login_if_needed(pg, step="login", timeout_seconds=RECOVERY_HOME_LOAD_TIMEOUT_SECONDS)

    def _logout(pg, retries: int = 1):
        last_err = None
        for attempt in range(1, retries + 2):
            try:
                time.sleep(1.0)
                LogoutPage(pg).logout()
                return True, None
            except Exception as exc:
                last_err = str(exc)
                if attempt <= retries:
                    time.sleep(1.0)
        return False, last_err

    def _cleanup_browser(browser, context, pg, *, flow_failed=False, logout_succeeded=False, logout_error=None) -> None:
        if pg is not None:
            try:
                BasePage(pg).stop_warning_auto_dismiss()
            except Exception:
                pass
        for obj in (pg, context, browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        print(
            f"  [INFO] Browser closed "
            f"(flow_failed={flow_failed}, logout_succeeded={logout_succeeded}, logout_error={logout_error})"
        )
        gc.collect()

    def _check_timeout(step: str) -> None:
        elapsed = time.monotonic() - started_at
        if elapsed > DEFAULT_TIMEOUT_SECONDS:
            raise PlaywrightTimeoutError(
                f"Test flow timeout after {int(elapsed)}s at step '{step}'"
            )

    print("\n[TEST] Full Estimate Flow (no storage upload)")
    print(f"  URL         : {base_url}")
    print(f"  Username    : {username}")
    print(f"  estimate_id : {estimate_id or '(new estimate)'}")
    print(f"  Branch      : {'existing-estimate' if use_existing_estimate else 'new-estimate'}")
    print(f"  Quote       : {json.dumps(quote_record, indent=4, default=str)}")
    print()

    browser = context = page = None
    invoice_path: Optional[Path] = None
    estimate_totals: Dict[int, str] = {}
    customer_selection_status: Optional[Dict[str, Any]] = None
    current_step = "starting"
    started_at = time.monotonic()
    flow_failed = False
    logout_succeeded = False
    logout_error: Optional[str] = None

    try:
        with _flow_lock:
            with sync_playwright() as playwright:
                csv_logger.init()

                try:
                    # ----------------------------------------------------------------
                    # Step 1 — Login
                    # ----------------------------------------------------------------
                    current_step = "login"
                    _check_timeout(current_step)
                    print(f"\n  [1] Logging in...")
                    browser, context, page = create_browser_page(playwright)
                    _login(page)
                    print(f"  [PASS] Logged in — URL: {_safe_url(page)}")

                    if use_existing_estimate:
                        # ----------------------------------------------------------------
                        # EXISTING ESTIMATE BRANCH
                        # mirrors: _open_existing_estimate() + invoice_tabs resume_from="estimate_summary"
                        # ----------------------------------------------------------------

                        # Step 2 — Navigate to quick-access to search for the estimate
                        current_step = "open_existing_estimate_quick_access"
                        _check_timeout(current_step)
                        print(f"\n  [2] Navigating to Quick Access to find estimate {estimate_id}...")
                        _navigate_with_recovery(page, quick_access_url, step=current_step)
                        print(f"  [PASS] Quick Access loaded — URL: {_safe_url(page)}")

                        # Step 3 — Search and open the existing estimate
                        current_step = "open_existing_estimate_search"
                        _check_timeout(current_step)
                        print(f"\n  [3] Searching and opening estimate {estimate_id}...")
                        EstimateSelectionPage(page).search_and_open_estimate(estimate_id)
                        print(f"  [PASS] Estimate opened — URL: {_safe_url(page)}")

                        # Step 4 — Switch to Estimate Summary tab and clear existing items
                        current_step = "estimate_summary_clear"
                        _check_timeout(current_step)
                        print(f"\n  [4] Switching to Estimate Summary tab and removing all items...")
                        summary_tab = EstimatedSummaryTab(page)
                        summary_tab.switch_to_tab()
                        summary_tab.remove_all_items()
                        print(f"  [PASS] Estimate Summary cleared — URL: {_safe_url(page)}")

                        customer_selection_status = {}

                        # Step 5 — Invoice tabs (resume from estimate_summary)
                        current_step = "invoice_tabs"
                        print(f"\n  [5] Completing Invoice tabs (existing estimate)...")
                        invoice_page = InvoicePage(page)
                        for attempt in range(2):
                            try:
                                _check_timeout(f"{current_step}_attempt_{attempt + 1}")
                                invoice_path, estimate_totals = invoice_page.complete_information_tabs(
                                    resume_from="estimate_summary",
                                    quote_record=quote_record,
                                    customer_selection_status=customer_selection_status,
                                )
                                break
                            except InvalidStockSearchError:
                                raise
                            except Exception:
                                if attempt == 0:
                                    print("  [WARN] Invoice tabs failed on first attempt; retrying once...")
                                    continue
                                raise
                        print(f"  [PASS] Invoice tabs done — PDF: {invoice_path}")
                        print(f"  [INFO] Estimate totals: {estimate_totals}")

                    else:
                        # ----------------------------------------------------------------
                        # NEW ESTIMATE BRANCH
                        # mirrors: quick_access -> create_estimate -> new_estimate_setup -> invoice_tabs
                        # ----------------------------------------------------------------

                        # Step 2 — Quick Access
                        current_step = "quick_access"
                        _check_timeout(current_step)
                        print(f"\n  [2] Navigating to Quick Access...")
                        _navigate_with_recovery(page, quick_access_url, step=current_step)
                        print(f"  [PASS] Quick Access loaded — URL: {_safe_url(page)}")

                        # Step 3 — Click Create Estimate
                        current_step = "create_estimate_click"
                        _check_timeout(current_step)
                        print(f"\n  [3] Clicking Create Estimate...")
                        EstimatePage(page).click_create_estimate_quick_access()
                        print(f"  [PASS] Create Estimate opened — URL: {_safe_url(page)}")

                        # Step 4 — New Estimate modal
                        current_step = "new_estimate_setup"
                        print(f"\n  [4] Completing New Estimate modal...")
                        new_estimate_page = NewEstimatePage(page)
                        for attempt in range(2):
                            try:
                                _check_timeout(f"{current_step}_attempt_{attempt + 1}")
                                customer_selection_status = new_estimate_page.complete_walk_in_digital_color(
                                    quote_record
                                )
                                break
                            except Exception:
                                if attempt == 0:
                                    print("  [WARN] New Estimate setup failed; retrying once...")
                                    continue
                                raise
                        print(f"  [PASS] New Estimate done — customer={customer_selection_status}")

                        # Step 5 — Invoice tabs
                        current_step = "invoice_tabs"
                        print(f"\n  [5] Completing Invoice tabs (new estimate)...")
                        invoice_page = InvoicePage(page)
                        for attempt in range(2):
                            try:
                                _check_timeout(f"{current_step}_attempt_{attempt + 1}")
                                invoice_path, estimate_totals = invoice_page.complete_information_tabs(
                                    resume_from="auto",
                                    quote_record=quote_record,
                                    customer_selection_status=customer_selection_status,
                                )
                                break
                            except InvalidStockSearchError:
                                raise
                            except Exception:
                                if attempt == 0:
                                    print("  [WARN] Invoice tabs failed; retrying once...")
                                    continue
                                raise
                        print(f"  [PASS] Invoice tabs done — PDF: {invoice_path}")
                        print(f"  [INFO] Estimate totals: {estimate_totals}")

                    # ----------------------------------------------------------------
                    # Step 6 — Logout (both branches)
                    # ----------------------------------------------------------------
                    current_step = "logout"
                    print(f"\n  [6] Logging out...")
                    logout_succeeded, logout_error = _logout(page, retries=1)
                    print(f"  [PASS] Logout — succeeded={logout_succeeded} error={logout_error}")

                    print("\n--- Result ---")
                    print(f"  status              : success")
                    print(f"  branch              : {'existing-estimate' if use_existing_estimate else 'new-estimate'}")
                    print(f"  invoice_file        : {invoice_path}")
                    print(f"  estimate_totals     : {estimate_totals}")
                    print(f"  customer_selection  : {customer_selection_status}")

                except InvalidLoginCredentialsError as exc:
                    flow_failed = True
                    print(f"\n[FAIL] Invalid credentials at step '{current_step}': {exc}")

                except PlaywrightTimeoutError as exc:
                    flow_failed = True
                    print(f"\n[FAIL] Timeout at step '{current_step}': {exc}")
                    if page is not None:
                        logout_succeeded, logout_error = _logout(page, retries=1)

                except Exception as exc:
                    import traceback
                    flow_failed = True
                    print(f"\n[FAIL] Error at step '{current_step}': {exc}")
                    traceback.print_exc()
                    if page is not None:
                        logout_succeeded, logout_error = _logout(page, retries=1)

        return not flow_failed

    finally:
        if invoice_path is not None:
            print(f"\n  [INFO] PDF kept at: {invoice_path} (not deleted — inspect manually)")
        _cleanup_browser(
            browser, context, page,
            flow_failed=flow_failed,
            logout_succeeded=logout_succeeded,
            logout_error=logout_error,
        )


if __name__ == "__main__":
    passed = test_full_flow()
    sys.exit(0 if passed else 1)
