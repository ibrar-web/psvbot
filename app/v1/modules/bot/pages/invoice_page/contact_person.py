import logging
from collections.abc import Mapping

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class ContactPersonTab(BasePage):
    ACCOUNT_INFO_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Account Information']]"
    JOB_DETAILS_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Job Details']]"
    CONTACT_FORM = "//form[.//a[@name='save_edit_contact_details_btn']]"
    INVOICE_FORM = "//form[.//a[@name='save_inv_address_btn']]"

    FIRST_NAME_INPUT = f"xpath={CONTACT_FORM}//input[@name='i_first_name_value']"
    EMAIL_INPUT = f"xpath={CONTACT_FORM}//input[@name='i_email_value']"
    INVALID_EMAIL_DIALOG = (
        "xpath=//div[contains(@class,'ui-confirmdialog')]"
        "[.//span[contains(normalize-space(), 'Invalid email address')]]"
    )
    INVALID_EMAIL_OK_BUTTON = (
        "xpath=//div[contains(@class,'ui-confirmdialog')]"
        "[.//span[contains(normalize-space(), 'Invalid email address')]]"
        "//button[.//span[normalize-space()='OK']]"
    )
    PHONE_INPUT = f"xpath={CONTACT_FORM}//input[@name='contact_phone_value']"

    INVOICE_COMPANY_INPUT = f"xpath={INVOICE_FORM}//input[@name='company']"
    INVOICE_STREET1_INPUT = f"xpath={INVOICE_FORM}//input[@name='street1']"
    INVOICE_CITY_INPUT = (
        f"xpath={INVOICE_FORM}//kendo-combobox[@name='city']//input[contains(@class,'k-input')]"
    )

    DONE_BUTTON = f"xpath={CONTACT_FORM}//a[@name='save_edit_contact_details_btn']"
    INVOICE_DONE_BUTTON = f"xpath={INVOICE_FORM}//a[@name='save_inv_address_btn']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][ContactPersonTab] {message}")
            logger.info(message)

    def fill_form(self, data: Mapping[str, str]) -> None:
        self._debug("Waiting for Account Information tab and contact fields")
        self.wait_for_visible(self.ACCOUNT_INFO_TAB)
        self.wait_for_visible(self.FIRST_NAME_INPUT)
        self._wait_for_spinner_overlay()

        self._safe_fill(self.FIRST_NAME_INPUT, data.get("contact_person", data.get("first_name", "")))
        self._fill_email_with_retry(data.get("contact_email", data.get("email", "")))
        self._safe_fill(self.PHONE_INPUT, data.get("contact_phone", data.get("phone", "")))

        self._debug("Saving contact details; filling invoice form")
        self.page.wait_for_timeout(1000)  # UI settle before invoice form becomes stable
        self._fill_invoice_address_with_retry(data, retries=2)

        self._debug("Clicking Done to save contact details")
        self.wait_for_visible(self.DONE_BUTTON)
        self.click(self.DONE_BUTTON)
        self._wait_for_spinner_overlay()

    def switch_to_job_details_tab(self) -> None:
        self._debug("Switching to Job Details tab")
        self.wait_for_spinner_to_disappear()
        self.click(self.JOB_DETAILS_TAB)
        self.page.wait_for_function(
            """() => {
                const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                const target = tabs.find(t => (t.innerText || "").includes("Job Details"));
                return !!target && target.getAttribute("aria-selected") === "true";
            }""",
            timeout=self._timeout_ms,
        )
        self.wait_for_spinner_to_disappear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_fill(self, selector: str, value: str, retries: int = 3) -> None:
        last_exc: Exception | None = None
        for _ in range(retries):
            try:
                self._wait_for_spinner_overlay()
                locator = self._loc(selector).first
                locator.wait_for(state="visible", timeout=self._timeout_ms)
                locator.fill(value or "", timeout=self._timeout_ms)
                self._wait_for_field_value(selector, value or "")
                self._wait_for_spinner_overlay()
                return
            except PlaywrightTimeoutError as exc:
                last_exc = exc
                self._wait_for_spinner_overlay()
                self.page.wait_for_timeout(400)
        if last_exc is not None:
            raise last_exc

    def _type_combo_value(self, selector: str, value: str) -> None:
        if not value:
            return
        self._wait_for_spinner_overlay()
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                locator = self._loc(selector).first
                locator.wait_for(state="visible", timeout=self._timeout_ms)
                locator.fill(value, timeout=self._timeout_ms)
                locator.press("Enter")
                self._wait_for_field_value(selector, value, allow_partial_match=True)
                self._wait_for_spinner_overlay()
                return
            except PlaywrightTimeoutError as exc:
                last_exc = exc
                self._wait_for_spinner_overlay()
                self.page.wait_for_timeout(400)
        if last_exc is not None:
            raise last_exc

    def _fill_invoice_address(self, data: Mapping[str, str]) -> None:
        self._debug("Filling invoice address details")
        self._wait_for_spinner_overlay()
        self._safe_fill(
            self.INVOICE_COMPANY_INPUT,
            data.get("company_name", data.get("account_name", "")),
        )
        self._safe_fill(self.INVOICE_STREET1_INPUT, data.get("street", ""))
        self._type_combo_value(self.INVOICE_CITY_INPUT, data.get("city", ""))
        self._debug("Invoice address filled; clicking Done to save invoice address")
        self.wait_for_visible(self.INVOICE_DONE_BUTTON)
        self.click(self.INVOICE_DONE_BUTTON)
        self._wait_for_spinner_overlay()

    def _fill_invoice_address_with_retry(self, data: Mapping[str, str], retries: int = 2) -> None:
        last_exc: Exception | None = None
        max_attempts = retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                self._debug(f"Invoice form fill attempt {attempt}/{max_attempts}")
                self._fill_invoice_address(data)
                return
            except Exception as exc:
                last_exc = exc
                self._debug(f"Invoice form fill failed on attempt {attempt}: {exc}")
                if attempt < max_attempts:
                    self.page.wait_for_timeout(1000)
        if last_exc is not None:
            raise last_exc

    def _fill_email_with_retry(self, email_value: str, retries: int = 2) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                self._safe_fill(self.EMAIL_INPUT, email_value or "")
                self._ensure_email_is_stable()
                return
            except Exception as exc:
                last_exc = exc
                self._dismiss_invalid_email_dialog_if_present()
                self._debug(f"Email fill failed on attempt {attempt}/{retries + 1}: {exc}")
                if attempt <= retries:
                    self.page.wait_for_timeout(400)
        if last_exc is not None:
            raise last_exc

    def _ensure_email_is_stable(self) -> None:
        locator = self._loc(self.EMAIL_INPUT).first
        locator.press("Tab")
        self._dismiss_invalid_email_dialog_if_present()
        self.page.wait_for_function(
            """() => {
                const input = document.querySelector("input[name='i_email_value']");
                if (!input) return false;
                const value = (input.value || "").trim();
                const hasBasicEmailShape = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(value);
                const invalidByClass = (input.className || "").toLowerCase().includes("ng-invalid");
                return hasBasicEmailShape && !invalidByClass;
            }""",
            timeout=self._timeout_ms,
        )
        self._dismiss_invalid_email_dialog_if_present()

    def _dismiss_invalid_email_dialog_if_present(self) -> bool:
        if not self.is_visible(self.INVALID_EMAIL_DIALOG):
            return False
        self._debug("Invalid email dialog detected, dismissing with OK")
        self.click(self.INVALID_EMAIL_OK_BUTTON)
        return True

    def _wait_for_field_value(
        self,
        selector: str,
        expected_value: str,
        allow_partial_match: bool = False,
    ) -> None:
        expected = (expected_value or "").strip()

        # Build the XPath for the JS lookup (strip leading "xpath=" prefix if present)
        raw_selector = selector.replace("xpath=", "", 1) if selector.startswith("xpath=") else None

        if raw_selector:
            self.page.wait_for_function(
                """([xp, expected, partial]) => {
                    const el = document.evaluate(
                      xp, document, null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE, null
                    ).singleNodeValue;
                    if (!el) return false;
                    const actual = (el.value || "").trim();
                    if (partial) {
                      const a = actual.toLowerCase();
                      const e = expected.toLowerCase();
                      return a === e || a.includes(e) || e.includes(a);
                    }
                    return actual === expected;
                }""",
                arg=[raw_selector, expected, allow_partial_match],
                timeout=self._timeout_ms,
            )
        else:
            locator = self._loc(selector).first
            locator.wait_for(state="visible", timeout=self._timeout_ms)
            actual = (locator.input_value() or "").strip()
            if allow_partial_match:
                a, e = actual.lower(), expected.lower()
                if not (a == e or a in e or e in a):
                    raise PlaywrightTimeoutError(
                        f"Field value mismatch: expected '{expected}', got '{actual}'"
                    )
            elif actual != expected:
                raise PlaywrightTimeoutError(
                    f"Field value mismatch: expected '{expected}', got '{actual}'"
                )

    def _wait_for_spinner_overlay(self) -> None:
        self.page.wait_for_function(
            """() => {
                const overlays = Array.from(document.querySelectorAll(".spinner-overlay"));
                if (!overlays.length) return true;
                return overlays.every(el => {
                  const style = window.getComputedStyle(el);
                  return style.display === "none" || style.visibility === "hidden" || el.offsetParent === null;
                });
            }""",
            timeout=self._timeout_ms,
        )
