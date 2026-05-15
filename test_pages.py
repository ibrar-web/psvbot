"""
Manual flow tester — same steps as run_estimate_flow() but skips the storage upload.

Usage:
    python test_pages.py

Set credentials in .env or as environment variables:
    PRINTSMITH_URL
    PRINTSMITH_USERNAME
    PRINTSMITH_PASSWORD
    PRINTSMITH_COMPANY      (optional)
    PRINTSMITH_HEADLESS     (default: false for local testing)
    PRINTSMITH_DEBUG        (default: true)
    PRINTSMITH_TIMEOUT_SECONDS (default: 120)
"""

import json
import os
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

load_dotenv()

# ---------------------------------------------------------------------------
# Credentials — read from .env, same keys the queue service uses
# ---------------------------------------------------------------------------
TENANT_CREDENTIALS = {
    "printsmith_url": os.getenv("PRINTSMITH_URL", ""),
    "username": os.getenv("PRINTSMITH_USERNAME", ""),
    "password": os.getenv("PRINTSMITH_PASSWORD", ""),
    "company": os.getenv("PRINTSMITH_COMPANY", ""),
}

# ---------------------------------------------------------------------------
# Quote record — same shape as what the queue service receives from the API
# ---------------------------------------------------------------------------
QUOTE_RECORD = {
    "_id": "test-001",
    "tenant_id": "test-tenant",
    "account_name": "Test Company",
    "company_name": "Test Company",
    "contact_person": "test user",
    "contact_email": "test@example.com",
    "contact_phone": "555-000-1234",
    "requirements": [
        {
            "stock_search": "][ GPA 15mil Rigid Vinyl",
            "quantity": "40",
            "size": "3x4",
            "sides": "duplex",
            "description": "40 double-sided 3x4 PVC name tags",
            "job_method": "Digital Color",
            "job_charges": [
                {"charge_name": "Lanyards",    "charge_price": 1.5,  "quantity": "40"},
                {"charge_name": "1 Hole Punch", "charge_price": 0.03, "quantity": "40"},
            ],
            "other_charges": [
                {"charge_name": "plastic sleeves", "charge_price": 0.5, "quantity": "40"},
            ],
            "rush_fee": 0,
        },
        {
            "stock_search": "Kelly Labels - DiversiPrint - Vinyl",
            "quantity": "40",
            "size": "3x4",
            "sides": "duplex",
            "description": "40 double-sided 3x4 vinyl name tags",
            "job_method": "Digital Color",
            "job_charges": [
                {"charge_name": "Lanyards",    "charge_price": 1.5,  "quantity": "40"},
                {"charge_name": "1 Hole Punch", "charge_price": 0.03, "quantity": "40"},
            ],
            "other_charges": [
                {"charge_name": "plastic sleeves", "charge_price": 0.5, "quantity": "40"},
            ],
            "rush_fee": 0,
        },
    ],
    "notes": "Require within thirty days",
}

_flow_lock = Lock()


def test_full_flow() -> bool:
    from app.v1.modules.bot import csv_logger
    from app.v1.modules.bot.base_page import BasePage
    from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS
    from app.v1.modules.bot.driver import create_browser_page
    from app.v1.modules.bot.pages.estimate_page import EstimatePage
    from app.v1.modules.bot.pages.invoice_page.invoice_page import InvoicePage
    from app.v1.modules.bot.pages.invoice_page.job_details import InvalidStockSearchError
    from app.v1.modules.bot.pages.login_page import InvalidLoginCredentialsError, LoginPage
    from app.v1.modules.bot.pages.logout_page import LogoutPage
    from app.v1.modules.bot.pages.new_estimate_page import NewEstimatePage
    from urllib.parse import urlparse

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

    def _logout(page, retries=1):
        for attempt in range(1, retries + 2):
            try:
                LogoutPage(page).logout()
                return True, None
            except Exception as exc:
                if attempt <= retries:
                    time.sleep(0.5)
                else:
                    return False, str(exc)

    username = str(TENANT_CREDENTIALS.get("username") or "").strip()
    password = str(TENANT_CREDENTIALS.get("password") or "").strip()
    company  = str(TENANT_CREDENTIALS.get("company") or "").strip()
    base_url = str(TENANT_CREDENTIALS.get("printsmith_url") or "").strip()
    quick_access_url = _build_quick_access_url(base_url)

    print("\n[TEST] Full Estimate Flow (no storage upload)")
    print(f"  URL      : {base_url}")
    print(f"  Username : {username}")
    print(f"  Quote    : {json.dumps(QUOTE_RECORD, indent=4)}")
    print()

    browser = page = None
    invoice_path: Optional[Path] = None
    estimate_totals: Dict[int, str] = {}
    customer_selection_status: Optional[Dict[str, Any]] = None
    current_step = "starting"
    started_at = time.monotonic()
    flow_timeout = DEFAULT_TIMEOUT_SECONDS

    def _check_timeout(step: str) -> None:
        elapsed = time.monotonic() - started_at
        if elapsed > flow_timeout:
            raise PlaywrightTimeoutError(
                f"Test flow timeout after {int(elapsed)}s at step '{step}'"
            )

    try:
        with _flow_lock:
            with sync_playwright() as playwright:
                csv_logger.init()

                # --- Login ---
                current_step = "login"
                _check_timeout(current_step)
                print(f"\n  [1] Logging in...")
                browser, _, page = create_browser_page(playwright)
                page.goto(base_url)
                login_page = LoginPage(page)
                login_page.login(username, password, company)
                login_page.wait_for_login_result()
                BasePage(page).wait_for_spinner_to_disappear()
                print(f"  [PASS] Logged in — URL: {page.url}")

                # --- Quick Access ---
                current_step = "quick_access"
                _check_timeout(current_step)
                print(f"\n  [2] Navigating to Quick Access...")
                page.goto(quick_access_url)
                BasePage(page).wait_for_spinner_to_disappear()
                print(f"  [PASS] Quick Access loaded — URL: {page.url}")

                # --- Create Estimate click ---
                current_step = "create_estimate_click"
                _check_timeout(current_step)
                print(f"\n  [3] Clicking Create Estimate...")
                EstimatePage(page).click_create_estimate_quick_access()
                print(f"  [PASS] Create Estimate opened — URL: {page.url}")

                # --- New Estimate modal ---
                current_step = "new_estimate_setup"
                print(f"\n  [4] Completing New Estimate modal...")
                new_estimate_page = NewEstimatePage(page)
                for attempt in range(2):
                    try:
                        _check_timeout(f"{current_step}_attempt_{attempt + 1}")
                        customer_selection_status = new_estimate_page.complete_walk_in_digital_color(
                            QUOTE_RECORD
                        )
                        break
                    except Exception:
                        if attempt == 0:
                            print("  [WARN] New Estimate setup failed; retrying once...")
                            continue
                        raise
                print(f"  [PASS] New Estimate done — customer={customer_selection_status}")

                # --- Invoice tabs ---
                current_step = "invoice_tabs"
                print(f"\n  [5] Completing Invoice tabs...")
                invoice_page = InvoicePage(page)
                for attempt in range(2):
                    try:
                        _check_timeout(f"{current_step}_attempt_{attempt + 1}")
                        invoice_path, estimate_totals = invoice_page.complete_information_tabs(
                            resume_from="auto",
                            quote_record=QUOTE_RECORD,
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

                # --- Logout ---
                current_step = "logout"
                print(f"\n  [6] Logging out...")
                logout_succeeded, logout_error = _logout(page, retries=1)
                print(f"  [PASS] Logout — succeeded={logout_succeeded} error={logout_error}")

                print("\n--- Result ---")
                print(f"  status         : success")
                print(f"  invoice_file   : {invoice_path}")
                print(f"  estimate_totals: {estimate_totals}")
                print(f"  customer_sel.  : {customer_selection_status}")
                return True

    except InvalidLoginCredentialsError as exc:
        print(f"\n[FAIL] Invalid credentials at step '{current_step}': {exc}")
        return False

    except PlaywrightTimeoutError as exc:
        print(f"\n[FAIL] Timeout at step '{current_step}': {exc}")
        if page:
            _logout(page, retries=1)
        return False

    except Exception as exc:
        import traceback
        print(f"\n[FAIL] Error at step '{current_step}': {exc}")
        traceback.print_exc()
        if page:
            _logout(page, retries=1)
        return False

    finally:
        if invoice_path is not None:
            print(f"\n  [INFO] PDF kept at: {invoice_path} (not deleted — inspect manually)")
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    passed = test_full_flow()
    sys.exit(0 if passed else 1)
