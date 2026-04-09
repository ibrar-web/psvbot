import logging
from pathlib import Path
import time
from typing import Any, Dict, Optional

from selenium.webdriver.common.by import By

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.pages.invoice_page.contact_person import ContactPersonTab
from app.v1.modules.bot.pages.invoice_page.estimated_summary import EstimatedSummaryTab
from app.v1.modules.bot.pages.invoice_page.job_details import (
    InvalidStockSearchError,
    JobDetailsTab,
)

logger = logging.getLogger(__name__)


class InvoicePage(BasePage):
    ACCOUNT_INFORMATION_TAB = "//li[@role='tab' and .//span[normalize-space()='Account Information']]"
    JOB_DETAILS_TAB = "//li[@role='tab' and .//span[normalize-space()='Job Details']]"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][InvoicePage] {message}")
            logger.info(message)

    def complete_information_tabs(
        self,
        resume_from: str = "auto",
        quote_record: Optional[Dict[str, Any]] = None,
        customer_selection_status: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        resume_from:
        - auto: if account info looks complete, continue from job details.
        - account: force account tab -> job tab flow.
        - job: skip account tab and start from job tab.
        """
        quote_record = quote_record or {}
        requirements = quote_record.get("requirements") or {}
        contact_data = {
            "account_name": quote_record.get("account_name", ""),
            "company_name": quote_record.get("company_name", quote_record.get("account_name", "")),
            "contact_person": quote_record.get("contact_person", ""),
            "contact_email": quote_record.get("contact_email", ""),
            "contact_phone": quote_record.get("contact_phone", ""),
            "street": quote_record.get("street", ""),
            "city": quote_record.get("city", ""),
        }
        job_data = {
            "stock_search_term": requirements.get(
                "stock_search_term",
                requirements.get("stock_search", ""),
            ),
            "price_breakup_quantity": requirements.get(
                "price_breakup_quantity",
                requirements.get("quantity", ""),
            ),
            "job_charges": requirements.get(
                "job_charges",
                [],
            ),
        }

        normalized = (resume_from or "auto").strip().lower()
        customer_selection_status = customer_selection_status or {}
        used_fallback_customer = bool(
            customer_selection_status.get("used_fallback_customer")
        )

        should_start_from_job = normalized == "job"
        if normalized == "auto" and not used_fallback_customer:
            self._debug(
                "Existing customer selected from dropdown; skipping Account Information and moving to Job Details"
            )
            should_start_from_job = True
        elif normalized == "auto" and self._is_account_information_complete():
            self._debug("Account Information already complete; resuming from Job Details")
            should_start_from_job = True

        if not should_start_from_job:
            self._retry_step(
                "account_information",
                lambda: self._complete_account_information(contact_data),
            )
        else:
            self._retry_step(
                "switch_to_job_details",
                self._switch_to_job_details_tab,
            )

        self._retry_step("job_details", lambda: self._complete_job_details(job_data))
        return self._retry_step(
            "estimate_summary_download",
            lambda: self._download_from_estimate_summary(customer_selection_status),
        )

    def _retry_step(self, step_name: str, callback, retries: int = 1):
        attempts = retries + 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            step_started_at = time.monotonic()
            try:
                if attempt > 1:
                    self._debug(f"Retrying step '{step_name}' ({attempt}/{attempts})")
                result = callback()
                elapsed = round(time.monotonic() - step_started_at, 3)
                self._debug(
                    f"Step '{step_name}' completed ({attempt}/{attempts}) in {elapsed}s"
                )
                return result
            except Exception as exc:
                last_exc = exc
                elapsed = round(time.monotonic() - step_started_at, 3)
                self._debug(
                    f"Step '{step_name}' failed ({attempt}/{attempts}) after {elapsed}s: {exc}"
                )
                if isinstance(exc, InvalidStockSearchError):
                    raise
                if attempt < attempts:
                    time.sleep(1)
        if last_exc is not None:
            raise last_exc

    def _complete_account_information(self, contact_data: Dict[str, Any]) -> None:
        self._debug("Completing Account Information tab")
        contact_person_tab = ContactPersonTab(self.driver, self.timeout)
        contact_person_tab.fill_form(contact_data)
        contact_person_tab.switch_to_job_details_tab()

    def _complete_job_details(self, job_data: Dict[str, Any]) -> None:
        self._debug("Completing Job Details tab")
        job_details_tab = JobDetailsTab(self.driver, self.timeout)
        job_details_tab.wait_until_active()
        job_details_tab.select_stock_from_picker(job_data)
        job_details_tab.configure_price_breakup(job_data)

    def _download_from_estimate_summary(
        self,
        customer_selection_status: Optional[Dict[str, Any]] = None,
    ) -> Path:
        estimated_summary_tab = EstimatedSummaryTab(self.driver, self.timeout)
        self._debug("Switching to Estimate Summary tab")
        estimated_summary_tab.switch_to_tab()
        self._debug(f"Estimate Summary tab active/visible: {estimated_summary_tab.is_visible()}")
        self._debug("Downloading invoice from Estimate Summary")
        return estimated_summary_tab.click_us685_eestimate_and_download(
            customer_selection_status=customer_selection_status,
        )

    def _switch_to_job_details_tab(self) -> None:
        self.wait_for_spinner_to_disappear()
        self.wait_for_visible(By.XPATH, self.JOB_DETAILS_TAB)
        self.click(By.XPATH, self.JOB_DETAILS_TAB)
        self.wait_for_spinner_to_disappear()

    def _is_account_information_complete(self) -> bool:
        """
        Heuristic failover check:
        if the core account/contact fields have non-empty values,
        we can safely continue from Job Details.
        """
        return bool(
            self.driver.execute_script(
                """
                const first = document.querySelector("input[name='i_first_name_value']");
                const email = document.querySelector("input[name='i_email_value']");
                const company = document.querySelector("input[name='company']");
                const values = [first?.value || "", email?.value || "", company?.value || ""]
                  .map(v => (v || "").trim());
                return values.every(v => v.length > 0);
                """
            )
        )
