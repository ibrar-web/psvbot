"""
Manual page tester — run this file directly to test the complete estimate flow.

Usage:
    python test_pages.py
"""

import os
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

URL = os.getenv("PRINTSMITH_URL", "")
USERNAME = os.getenv("PRINTSMITH_USERNAME", "")
PASSWORD = os.getenv("PRINTSMITH_PASSWORD", "")
COMPANY = os.getenv("PRINTSMITH_COMPANY", "")
HEADLESS = os.getenv("PRINTSMITH_HEADLESS", "false").strip().lower() in {"1", "true", "yes", "on"}
TIMEOUT = int(os.getenv("PRINTSMITH_TIMEOUT_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Dummy quote record — edit these values to match your test environment
# ---------------------------------------------------------------------------
DUMMY_QUOTE = {
    "_id": "test-001",
    "tenant_id": "test-tenant",
    "account_name": "Test Company",
    "company_name": "Test Company",
    "contact_person": "john doe",       # use an existing customer name or "walk-in"
    "contact_email": "test@example.com",
    "contact_phone": "555-000-1234",
    "requirements": {
        "stock_search_term": "gpa",    # partial name of a stock that exists in PrintSmith
        "price_breakup_quantity": "100",
        "job_charges": [],             # e.g. ["Design", "Lamination"] or leave empty
    },
}


def _build_quick_access_url(base_url: str) -> str:
    from urllib.parse import urlparse

    if "/PrintSmith/PrintSmith.html" in base_url:
        return base_url.replace(
            "/PrintSmith/PrintSmith.html",
            "/PrintSmith/nextgen/en_US/#/quick-access",
        )
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/PrintSmith/nextgen/en_US/#/quick-access"
    return base_url


# ---------------------------------------------------------------------------
# Individual step tests
# ---------------------------------------------------------------------------

def test_login():
    print("\n[TEST] Login Page")
    print(f"  URL      : {URL}")
    print(f"  Username : {USERNAME}")
    print(f"  Headless : {HEADLESS}")

    from app.v1.modules.bot.driver import create_browser_page
    from app.v1.modules.bot.pages.login_page import LoginPage

    with sync_playwright() as playwright:
        browser, context, page = create_browser_page(playwright)
        try:
            page.goto(URL, timeout=TIMEOUT * 1000)
            login_page = LoginPage(page, timeout=TIMEOUT)
            login_page.login(USERNAME, PASSWORD, COMPANY)
            success = login_page.wait_for_login_result()

            if success:
                print(f"  [PASS] Login successful — landed on: {page.url}")
            else:
                print(f"  [FAIL] Login failed — still on: {page.url}")
        except Exception as e:
            print(f"  [ERROR] {e}")
            success = False
        finally:
            browser.close()

    return success


def test_full_flow():
    """
    Runs the complete estimate flow end-to-end with DUMMY_QUOTE data:
      1. Login
      2. Navigate to Quick Access
      3. Click Create Estimate
      4. New Estimate modal  — choose customer + Digital Color
      5. Invoice page tabs   — Account Information (if walk-in) → Job Details → Estimate Summary
      6. Download the US685 E-Estimate PDF (saved to a temp directory)
      7. Logout
    """
    print("\n[TEST] Full Estimate Flow")
    print(f"  URL      : {URL}")
    print(f"  Username : {USERNAME}")
    print(f"  Headless : {HEADLESS}")
    print(f"  Quote    : {DUMMY_QUOTE}")

    from app.v1.modules.bot.base_page import BasePage
    from app.v1.modules.bot.driver import create_browser_page
    from app.v1.modules.bot.pages.estimate_page import EstimatePage
    from app.v1.modules.bot.pages.invoice_page.invoice_page import InvoicePage
    from app.v1.modules.bot.pages.login_page import LoginPage
    from app.v1.modules.bot.pages.logout_page import LogoutPage
    from app.v1.modules.bot.pages.new_estimate_page import NewEstimatePage

    quick_access_url = _build_quick_access_url(URL)

    with sync_playwright() as playwright:
        browser, context, page = create_browser_page(playwright)
        invoice_path = None
        customer_selection_status = None
        try:
            # --- Step 1: Login ---
            print("\n  [1/7] Logging in...")
            page.goto(URL, timeout=TIMEOUT * 1000)
            login_page = LoginPage(page, timeout=TIMEOUT)
            login_page.login(USERNAME, PASSWORD, COMPANY)
            success = login_page.wait_for_login_result()
            if not success:
                print(f"  [FAIL] Login failed — URL: {page.url}")
                return False
            BasePage(page, timeout=TIMEOUT).wait_for_spinner_to_disappear()
            print(f"  [PASS] Logged in — URL: {page.url}")

            # --- Step 2: Quick Access page ---
            print("\n  [2/7] Navigating to Quick Access...")
            page.goto(quick_access_url, timeout=TIMEOUT * 1000)
            BasePage(page, timeout=TIMEOUT).wait_for_spinner_to_disappear()
            print(f"  [PASS] Quick Access loaded — URL: {page.url}")

            # --- Step 3: Click Create Estimate ---
            print("\n  [3/7] Clicking Create Estimate...")
            estimate_page = EstimatePage(page, timeout=TIMEOUT)
            estimate_page.click_create_estimate_quick_access()
            print(f"  [PASS] Create Estimate opened — URL: {page.url}")

            # --- Step 4: New Estimate modal (customer + job method) ---
            print("\n  [4/7] Completing New Estimate modal (customer + Digital Color)...")
            new_estimate_page = NewEstimatePage(page, timeout=TIMEOUT)
            customer_selection_status = new_estimate_page.complete_walk_in_digital_color(DUMMY_QUOTE)
            print(f"  [PASS] New Estimate complete — customer_selection={customer_selection_status}")
            print(f"         URL: {page.url}")

            # --- Step 5 + 6: Invoice tabs → download PDF ---
            print("\n  [5/7] Completing Invoice tabs (Account Info → Job Details → Estimate Summary)...")
            invoice_page = InvoicePage(page, timeout=TIMEOUT)
            invoice_path = invoice_page.complete_information_tabs(
                resume_from="auto",
                quote_record=DUMMY_QUOTE,
                customer_selection_status=customer_selection_status,
            )
            print(f"  [PASS] Invoice tabs done — PDF saved to: {invoice_path}")

            # --- Step 7: Logout ---
            print("\n  [7/7] Logging out...")
            logout_page = LogoutPage(page, timeout=TIMEOUT)
            logout_page.logout()
            print("  [PASS] Logged out")

            return True

        except Exception as e:
            print(f"\n  [ERROR] Flow failed: {e}")
            import traceback
            traceback.print_exc()
            try:
                print("\n  [CLEANUP] Attempting logout after failure...")
                logout_page = LogoutPage(page, timeout=TIMEOUT)
                logout_page.logout()
                print("  [CLEANUP] Logged out successfully")
            except Exception as logout_err:
                print(f"  [CLEANUP] Logout also failed: {logout_err}")
            return False

        finally:
            if invoice_path is not None:
                print(f"\n  [INFO] PDF available at: {invoice_path}")
                print("         (not deleted — inspect it manually)")
            browser.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = {
        "full_flow": test_full_flow(),
    }

    print("\n--- Results ---")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name:<20} {status}")

    if not all(results.values()):
        sys.exit(1)
