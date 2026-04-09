import logging
import time
from collections.abc import Mapping

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class ContactPersonTab(BasePage):
    ACCOUNT_INFO_TAB = "//li[@role='tab' and .//span[normalize-space()='Account Information']]"
    JOB_DETAILS_TAB = "//li[@role='tab' and .//span[normalize-space()='Job Details']]"
    CONTACT_FORM = "//form[.//a[@name='save_edit_contact_details_btn']]"
    INVOICE_FORM = "//form[.//a[@name='save_inv_address_btn']]"

    # Contact Person
    FIRST_NAME_INPUT = CONTACT_FORM + "//input[@name='i_first_name_value']"
    # LAST_NAME_INPUT = CONTACT_FORM + "//input[@name='i_last_name_value']"
    # Contact Email
    EMAIL_INPUT = CONTACT_FORM + "//input[@name='i_email_value']"
    INVALID_EMAIL_DIALOG = (
        "//div[contains(@class,'ui-confirmdialog')]"
        "[.//span[contains(normalize-space(), 'Invalid email address')]]"
    )
    INVALID_EMAIL_OK_BUTTON = INVALID_EMAIL_DIALOG + "//button[.//span[normalize-space()='OK']]"
    # WEBSITE_INPUT = CONTACT_FORM + "//input[@name='i_website_value']"
    # Contact Phone
    PHONE_INPUT = CONTACT_FORM + "//input[@name='contact_phone_value']"
    # MOBILE_INPUT = CONTACT_FORM + "//input[@name='contact_mobile_value']"
    # FAX_INPUT = CONTACT_FORM + "//input[@name='i_fax_value']"
    # OTHER_INPUT = CONTACT_FORM + "//input[@name='i_other_value']"
    # JOB_TITLE_INPUT = CONTACT_FORM + "//kendo-combobox[@name='jobTitle']//input[contains(@class,'k-input')]"

    INVOICE_COMPANY_INPUT = INVOICE_FORM + "//input[@name='company']"
    INVOICE_STREET1_INPUT = INVOICE_FORM + "//input[@name='street1']"
    INVOICE_STREET2_INPUT = INVOICE_FORM + "//input[@name='street2']"
    INVOICE_CITY_INPUT = INVOICE_FORM + "//kendo-combobox[@name='city']//input[contains(@class,'k-input')]"
    INVOICE_STATE_INPUT = (
        INVOICE_FORM + "//kendo-combobox[@name='invAddState']//input[contains(@class,'k-input')]"
    )
    INVOICE_ZIP_INPUT = INVOICE_FORM + "//kendo-combobox[@name='invAddZip']//input[contains(@class,'k-input')]"
    INVOICE_COUNTRY_INPUT = (
        INVOICE_FORM + "//kendo-combobox[@name='invAddCountry']//input[contains(@class,'k-input')]"
    )

    DONE_BUTTON = CONTACT_FORM + "//a[@name='save_edit_contact_details_btn']"
    INVOICE_DONE_BUTTON = INVOICE_FORM + "//a[@name='save_inv_address_btn']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][ContactPersonTab] {message}")
            logger.info(message)

    def fill_form(self, data: Mapping[str, str]) -> None:
        self._debug("Waiting for Account Information tab and contact fields")
        self.wait_for_visible(By.XPATH, self.ACCOUNT_INFO_TAB)
        self.wait_for_visible(By.XPATH, self.FIRST_NAME_INPUT)
        self._wait_for_spinner_overlay()

        self._safe_type(
            By.XPATH,
            self.FIRST_NAME_INPUT,
            data.get("contact_person", data.get("first_name", "")),
        )
        self._fill_email_with_retry(data.get("contact_email", data.get("email", "")))
        self._safe_type(By.XPATH, self.PHONE_INPUT, data.get("contact_phone", data.get("phone", "")))
        # self._safe_type(By.XPATH, self.LAST_NAME_INPUT, data.get("last_name", ""))
        # self._safe_type(By.XPATH, self.WEBSITE_INPUT, data.get("website", ""))
        # self._safe_type(By.XPATH, self.MOBILE_INPUT, data.get("mobile", ""))
        # self._safe_type(By.XPATH, self.FAX_INPUT, data.get("fax", ""))
        # self._safe_type(By.XPATH, self.OTHER_INPUT, data.get("other_email", ""))

        self._debug("Saving contact details")
        # UI needs a brief settle time before invoice form becomes stable.
        time.sleep(1)
        self._fill_invoice_address_with_retry(data, retries=2)
        # Keep a brief pause after successful invoice fill before tab switch.
        # time.sleep(1)

    def switch_to_job_details_tab(self) -> None:
        self._debug("Switching to Job Details tab")
        self.wait_for_spinner_to_disappear()
        self.click(By.XPATH, self.JOB_DETAILS_TAB)
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                    const target = tabs.find(t => (t.innerText || "").includes("Job Details"));
                    return !!target && target.getAttribute("aria-selected") === "true";
                    """
                )
            )
        )
        self.wait_for_spinner_to_disappear()

    def _type_combo_value(self, locator: str, value: str) -> None:
        if not value:
            return
        self._wait_for_spinner_overlay()
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                element = self.wait_for_visible(By.XPATH, locator)
                self._replace_input_value(element, value)
                element.send_keys(Keys.ENTER)
                self._wait_for_field_value(By.XPATH, locator, value, allow_partial_match=True)
                self._wait_for_spinner_overlay()
                return
            except (ElementClickInterceptedException, StaleElementReferenceException, TimeoutException) as exc:
                last_exc = exc
                self._wait_for_spinner_overlay()
                time.sleep(0.4)
        if last_exc is not None:
            raise last_exc

    def _fill_invoice_address(self, data: Mapping[str, str]) -> None:
        self._debug("Filling invoice address details")
        self._wait_for_spinner_overlay()
        self._safe_type(
            By.XPATH,
            self.INVOICE_COMPANY_INPUT,
            data.get("company_name", data.get("contact_person", "")),
        )
        self._safe_type(By.XPATH, self.INVOICE_STREET1_INPUT, data.get("street", ""))
        # self._safe_type(By.XPATH, self.INVOICE_STREET2_INPUT, data.get("street2", ""))

        self._type_combo_value(self.INVOICE_CITY_INPUT, data.get("city", ""))
        # self._type_combo_value(self.INVOICE_STATE_INPUT, data.get("state", ""))
        # self._type_combo_value(self.INVOICE_ZIP_INPUT, data.get("zip", ""))
        # self._type_combo_value(self.INVOICE_COUNTRY_INPUT, data.get("country", ""))

        self._debug("Saving invoice address details")
        self._wait_for_spinner_overlay()
        # self.click(By.XPATH, self.INVOICE_DONE_BUTTON)

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
                    time.sleep(1)

        if last_exc is not None:
            raise last_exc

    def _safe_type(self, by: By, locator: str, value: str, retries: int = 3) -> None:
        last_exc: Exception | None = None
        for _ in range(retries):
            try:
                self._wait_for_spinner_overlay()
                element = self.wait_for_visible(by, locator)
                self._replace_input_value(element, value)
                self._wait_for_field_value(by, locator, value)
                self._wait_for_spinner_overlay()
                return
            except (ElementClickInterceptedException, StaleElementReferenceException, TimeoutException) as exc:
                last_exc = exc
                self._wait_for_spinner_overlay()
                time.sleep(0.4)
        if last_exc is not None:
            raise last_exc

    def _replace_input_value(self, element, value: str) -> None:
        try:
            element.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", element)

        element.clear()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        if value:
            element.send_keys(value)
        current = (element.get_attribute("value") or "").strip()
        expected = (value or "").strip()
        if current != expected:
            self.driver.execute_script(
                """
                const input = arguments[0];
                const newValue = arguments[1];
                input.value = newValue;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                element,
                value or "",
            )

    def _ensure_email_is_stable(self) -> None:
        email_input = self.wait_for_visible(By.XPATH, self.EMAIL_INPUT)
        # Blur the email field so PSV validation runs before we move to phone.
        email_input.send_keys(Keys.TAB)
        self._dismiss_invalid_email_dialog_if_present()
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const input = document.querySelector("input[name='i_email_value']");
                    if (!input) return false;
                    const value = (input.value || "").trim();
                    const hasBasicEmailShape = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(value);
                    const invalidByClass = (input.className || "").toLowerCase().includes("ng-invalid");
                    return hasBasicEmailShape && !invalidByClass;
                    """
                )
            )
        )
        self._dismiss_invalid_email_dialog_if_present()

    def _fill_email_with_retry(self, email_value: str, retries: int = 2) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 2):
            try:
                self._safe_type(By.XPATH, self.EMAIL_INPUT, email_value or "")
                self._ensure_email_is_stable()
                return
            except Exception as exc:
                last_exc = exc
                self._dismiss_invalid_email_dialog_if_present()
                self._debug(f"Email fill failed on attempt {attempt}/{retries + 1}: {exc}")
                if attempt <= retries:
                    time.sleep(0.4)
        if last_exc is not None:
            raise last_exc

    def _dismiss_invalid_email_dialog_if_present(self) -> bool:
        if not self.is_visible(By.XPATH, self.INVALID_EMAIL_DIALOG):
            return False
        self._debug("Invalid email dialog detected, dismissing with OK")
        self.click(By.XPATH, self.INVALID_EMAIL_OK_BUTTON)
        return True

    def _wait_for_field_value(
        self,
        by: By,
        locator: str,
        expected_value: str,
        allow_partial_match: bool = False,
    ) -> None:
        expected = (expected_value or "").strip()

        def value_matches(driver) -> bool:
            element = driver.find_element(by, locator)
            actual = (element.get_attribute("value") or "").strip()
            if allow_partial_match:
                actual_normalized = actual.lower()
                expected_normalized = expected.lower()
                return (
                    actual_normalized == expected_normalized
                    or actual_normalized in expected_normalized
                    or expected_normalized in actual_normalized
                )
            return actual == expected

        WebDriverWait(self.driver, self.timeout).until(value_matches)

    def _wait_for_spinner_overlay(self) -> None:
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const overlays = Array.from(document.querySelectorAll(".spinner-overlay"));
                    if (!overlays.length) return true;
                    return overlays.every(el => {
                      const style = window.getComputedStyle(el);
                      return style.display === "none" || style.visibility === "hidden" || el.offsetParent === null;
                    });
                    """
                )
            )
        )
