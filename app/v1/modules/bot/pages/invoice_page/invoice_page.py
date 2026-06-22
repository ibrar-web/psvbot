import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.pages.new_estimate_page import NewEstimatePage
from app.v1.modules.bot.pages.invoice_page.contact_person import ContactPersonTab
from app.v1.modules.bot.pages.invoice_page.estimated_summary import EstimatedSummaryTab
from app.v1.modules.bot.pages.invoice_page.job_details import (
    InvalidStockSearchError,
    JobDetailsTab,
)

logger = logging.getLogger(__name__)


class InvoicePage(BasePage):
    ACCOUNT_INFORMATION_TAB = (
        "xpath=//li[@role='tab' and .//span[normalize-space()='Account Information']]"
    )
    JOB_DETAILS_TAB = (
        "xpath=//li[@role='tab' and .//span[normalize-space()='Job Details']]"
    )
    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][InvoicePage] {message}")
        logger.info(message)

    def complete_information_tabs(
        self,
        resume_from: str = "auto",
        quote_record: Optional[Dict[str, Any]] = None,
        customer_selection_status: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Path, Dict[int, str]]:
        """
        resume_from:
        - auto: if account info looks complete, continue from job details.
        - account: force account tab -> job tab flow.
        - job: skip account tab and start from job tab.
        - estimate_summary: start on Estimate Summary, add the first job,
          choose its job method, then continue from job details.

        Returns:
            Tuple of (invoice_path, estimate_totals) where estimate_totals
            maps 1-based requirement index to the collect-total value captured
            before other charges are added.
        """
        quote_record = quote_record or {}
        requirements = self._normalize_requirements(quote_record)
        self._debug(f"Invoice flow quote_record={quote_record}")
        self._debug(f"Invoice flow requirements={requirements}")
        contact_data = {
            "account_name": quote_record.get("account_name", ""),
            "company_name": quote_record.get(
                "company_name", quote_record.get("account_name", "")
            ),
            "contact_person": quote_record.get("contact_person", ""),
            "contact_email": quote_record.get("contact_email", ""),
            "contact_phone": quote_record.get("contact_phone", ""),
            "street": quote_record.get("street", ""),
            "city": quote_record.get("city", ""),
        }

        normalized = (resume_from or "auto").strip().lower()
        requirement_customer_status = customer_selection_status or {}

        self.start_warning_auto_dismiss()
        estimate_totals: Dict[int, str] = {}
        try:
            for index, requirement in enumerate(requirements):
                if index == 0 and normalized in {
                    "estimate_summary",
                    "summary",
                    "summary_add_job",
                    "add_job",
                }:
                    requirement_customer_status = self._retry_step(
                        "add_job_1",
                        lambda requirement=requirement: self._add_job_and_prepare_requirement(
                            requirement
                        ),
                    )
                    normalized = "job"

                job_data = self._build_job_data(quote_record, requirement)
                self._debug(
                    f"Invoice flow requirement {index + 1}/{len(requirements)} job_data={job_data}"
                )
                self._complete_single_requirement(
                    contact_data=contact_data,
                    job_data=job_data,
                    resume_from=normalized,
                    customer_selection_status=requirement_customer_status,
                )

                # Collect estimate totals BEFORE other charges alter the values
                estimated_summary_tab = EstimatedSummaryTab(self.page, self.timeout)
                try:
                    current_totals = estimated_summary_tab.collect_estimate_totals()
                    if current_totals:
                        scraped_total = current_totals.get(max(current_totals.keys()), "")
                        other_charges = job_data.get("other_charges") or []
                        if isinstance(other_charges, dict):
                            other_charges = [other_charges]
                        try:
                            base = float(
                                str(scraped_total).replace("$", "").replace(",", "").strip() or "0"
                            )
                            for c in other_charges:
                                if not isinstance(c, dict):
                                    continue
                                raw = c.get("charge_price") or c.get("price") or ""
                                base += float(str(raw).replace("$", "").replace(",", "").strip() or "0")
                            total_with_tax = round(base + (base * 0.0863), 2)
                            estimate_totals[index + 1] = f"{total_with_tax:.2f}"
                        except (ValueError, TypeError):
                            estimate_totals[index + 1] = scraped_total
                        self._debug(
                            f"Requirement {index + 1} estimate total collected: "
                            f"{estimate_totals[index + 1]}"
                        )
                except Exception as exc:
                    self._debug(
                        f"Could not collect estimate total for requirement {index + 1}: {exc}"
                    )

                self._retry_step(
                    f"other_charges_{index + 1}",
                    lambda job_data=job_data: self._complete_other_charges(job_data),
                )
                if index < len(requirements) - 1:
                    next_requirement = requirements[index + 1]
                    requirement_customer_status = self._retry_step(
                        f"add_job_{index + 2}",
                        lambda: self._add_job_and_prepare_requirement(
                            next_requirement,
                        ),
                    )
                    normalized = "job"

            invoice_path = self._retry_step(
                "estimate_summary_download",
                lambda: self._download_from_estimate_summary(
                    requirement_customer_status,
                    quote_record=quote_record,
                ),
            )
            return invoice_path, estimate_totals
        finally:
            self.stop_warning_auto_dismiss()

    def _normalize_requirements(self, quote_record: Dict[str, Any]) -> list[Dict[str, Any]]:
        requirements = quote_record.get("requirements") or []
        if isinstance(requirements, dict):
            requirements = [requirements]

        normalized_requirements = []
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            normalized_requirements.append(requirement)

        if normalized_requirements:
            return normalized_requirements
        return [{}]

    def _build_job_data(
        self,
        quote_record: Dict[str, Any],
        requirement: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "description": requirement.get("description", ""),
            "stock_search_term": requirement.get(
                "stock_search_term",
                requirement.get("stock_search", ""),
            ),
            "price_breakup_quantity": requirement.get(
                "price_breakup_quantity",
                requirement.get("quantity", ""),
            ),
            "job_charges": requirement.get("job_charges", []),
            "other_charges": requirement.get(
                "other_charges",
                requirement.get("other_chrages", []),
            ),
            "notes": quote_record.get("notes", requirement.get("notes", "")),
            "sides": requirement.get("sides", ""),
            "size": requirement.get("size", ""),
            "job_method": requirement.get("job_method", ""),
            "agent_total": requirement.get("total", ""),
            "vendor_name": requirement.get("vendor_name", ""),
        }

    def _complete_single_requirement(
        self,
        *,
        contact_data: Dict[str, Any],
        job_data: Dict[str, Any],
        resume_from: str,
        customer_selection_status: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        customer_selection_status = customer_selection_status or {}
        used_fallback_customer = bool(
            customer_selection_status.get("used_fallback_customer")
        )

        should_start_from_job = resume_from == "job"
        if resume_from == "auto" and not used_fallback_customer:
            self._debug(
                "Existing customer selected from dropdown; skipping Account Information and moving to Job Details"
            )
            should_start_from_job = True
        elif resume_from == "auto" and self._is_account_information_complete():
            self._debug(
                "Account Information already complete; resuming from Job Details"
            )
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

        self._retry_step(
            "job_details",
            lambda: self._complete_job_details(job_data),
        )

    def _add_job_and_prepare_requirement(
        self,
        requirement: Dict[str, Any],
    ) -> Dict[str, Any]:
        estimated_summary_tab = EstimatedSummaryTab(self.page, self.timeout)
        estimated_summary_tab.click_add_job()
        new_estimate_page = NewEstimatePage(self.page, self.timeout)
        selection_status = new_estimate_page.complete_existing_customer_job_method(
            requirement.get("job_method", "")
        )
        self.wait_for_spinner_to_disappear()
        self._switch_to_job_details_tab()
        return selection_status

    def _complete_other_charges(self, job_data: Dict[str, Any]) -> None:
        other_charges = self._normalize_other_charges(job_data.get("other_charges"))
        if not other_charges:
            self._debug("No other charges provided for this requirement")
            return

        estimated_summary_tab = EstimatedSummaryTab(self.page, self.timeout)
        new_estimate_page = NewEstimatePage(self.page, self.timeout)
        job_details_tab = JobDetailsTab(self.page, self.timeout)

        for index, charge in enumerate(other_charges, start=1):
            self._debug(
                f"Adding other charge {index}/{len(other_charges)} as Charges Only job"
            )
            estimated_summary_tab.click_add_job()
            new_estimate_page.complete_existing_customer_job_method("Charges Only")
            self.wait_for_spinner_to_disappear()
            self._switch_to_job_details_tab()
            job_details_tab.wait_until_charges_only_active()
            job_details_tab.fill_charges_only_job(charge)
            estimated_summary_tab.switch_to_tab()

    def _normalize_other_charges(self, other_charges: Any) -> list[Any]:
        if not other_charges:
            return []
        if isinstance(other_charges, dict):
            return [other_charges]
        if isinstance(other_charges, list):
            return other_charges
        if isinstance(other_charges, tuple):
            return list(other_charges)
        return [other_charges]

    def _retry_step(self, step_name: str, callback, retries: int = 1):
        attempts = retries + 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if attempt > 1:
                    self._debug(f"Retrying step '{step_name}' ({attempt}/{attempts})")
                result = callback()
                return result
            except Exception as exc:
                last_exc = exc
                self._debug(f"Step '{step_name}' failed ({attempt}/{attempts}): {exc}")
                if isinstance(exc, InvalidStockSearchError):
                    raise
                if attempt < attempts:
                    time.sleep(1)
        if last_exc is not None:
            raise last_exc

    def _complete_account_information(self, contact_data: Dict[str, Any]) -> None:
        self._debug("Completing Account Information tab")
        contact_person_tab = ContactPersonTab(self.page, self.timeout)
        contact_person_tab.fill_form(contact_data)
        contact_person_tab.switch_to_job_details_tab()

    def _complete_job_details(self, job_data: Dict[str, Any]) -> None:
        self._debug("Completing Job Details tab")
        job_details_tab = JobDetailsTab(self.page, self.timeout)

        method = (job_data.get("job_method") or "").strip().lower()
        if method == "sublet":
            self._complete_sublet_job_details(job_details_tab, job_data)
            return

        job_details_tab.wait_until_active()
        job_details_tab.fill_job_description(job_data)
        job_details_tab.select_stock_from_picker(job_data)
        job_details_tab.add_size(job_data.get("size", ""))
        job_details_tab.add_notes(job_data.get("notes", ""))
        job_details_tab.select_bleed()
        job_details_tab.select_sides(job_data.get("sides", ""))
        job_details_tab.configure_price_breakup(job_data)

    def _complete_sublet_job_details(
        self,
        job_details_tab: JobDetailsTab,
        job_data: Dict[str, Any],
    ) -> None:
        """Sublet: same Job Details form, fewer steps.
        """
        self._debug("Completing Job Details tab for Sublet job method")
        job_details_tab.wait_until_active(job_method="sublet")
        job_details_tab.fill_job_description(job_data, job_method="sublet")
        job_details_tab.select_vendor(job_data.get("vendor_name", ""))
        job_details_tab.sublet_price_breakup(job_data)

    def _download_from_estimate_summary(
        self,
        customer_selection_status: Optional[Dict[str, Any]] = None,
        quote_record: Optional[Dict[str, Any]] = None,
    ) -> Path:
        estimated_summary_tab = EstimatedSummaryTab(self.page, self.timeout)
        self._debug("Switching to Estimate Summary tab")
        estimated_summary_tab.switch_to_tab()
        self._debug(
            f"Estimate Summary tab active/visible: {estimated_summary_tab.is_visible()}"
        )
        # Set the wanted/due date before finalizing the estimate.
        estimated_summary_tab.set_wanted_date(quote_record or {})
        self._debug("Downloading invoice from Estimate Summary")
        return estimated_summary_tab.click_us685_eestimate_and_download(
            customer_selection_status=customer_selection_status,
        )

    def _switch_to_job_details_tab(self) -> None:
        self.wait_for_spinner_to_disappear()
        self.wait_for_visible(self.JOB_DETAILS_TAB)
        self.click(self.JOB_DETAILS_TAB)
        self.wait_for_spinner_to_disappear()

    def _is_account_information_complete(self) -> bool:
        return bool(
            self.page.evaluate(
                """() => {
                    const first = document.querySelector("input[name='i_first_name_value']");
                    const email = document.querySelector("input[name='i_email_value']");
                    const company = document.querySelector("input[name='company']");
                    const values = [first?.value || "", email?.value || "", company?.value || ""]
                      .map(v => (v || "").trim());
                    return values.every(v => v.length > 0);
                }"""
            )
        )
