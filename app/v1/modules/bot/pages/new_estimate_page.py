import logging
from collections.abc import Mapping
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class NewEstimatePage(BasePage):
    INVOICE_PAGE_URL_PART = "#/invoicing/invoice-page"
    CHOOSE_CUSTOMER_LABEL = (
        "xpath=//label[@name='choose_type' and normalize-space()='Choose Customer']"
    )
    CHOOSE_CUSTOMER_INPUT = (
        "xpath=//kendo-combobox[@name='choose_type_value']//input"
        " | //label[@name='choose_type']/following::kendo-combobox[1]//input"
    )
    NEXT_STEP_BUTTON = "xpath=//button[@name='next_step_button']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][NewEstimatePage] {message}")
            logger.info(message)

    def complete_walk_in_job_method(
        self,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._wait_for_modal_ready()
        payload = data or {}
        requirements = payload.get("requirements") or []
        first_requirement = requirements[0] if isinstance(requirements, list) and requirements else {}
        if not isinstance(first_requirement, Mapping):
            first_requirement = {}
        selection_status = self._select_walk_in_customer(payload)
        self._select_job_method(
            str(
                payload.get("job_method")
                or first_requirement.get("job_method")
                or "Digital Color"
            )
        )
        self._wait_for_invoice_page()
        return selection_status

    def complete_existing_customer_job_method(
        self,
        job_method: str | None = None,
    ) -> dict[str, Any]:
        self._debug("Continuing new estimate with existing selected customer")
        self.wait_for_spinner_to_disappear()
        self._select_job_method(str(job_method or "Digital Color"))
        self._wait_for_invoice_page()
        return {
            "used_fallback_customer": False,
            "requested_customer_name": None,
            "selected_customer_name": None,
            "fallback_reason": None,
        }

    def complete_walk_in_digital_color(
        self,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.complete_walk_in_job_method(data)

    def _wait_for_modal_ready(self) -> None:
        self._debug("Waiting for Choose Customer form on invoice page")
        self.wait_for_spinner_to_disappear()
        self.wait_for_visible(self.CHOOSE_CUSTOMER_LABEL)
        self.wait_for_visible(self.CHOOSE_CUSTOMER_INPUT)

    def _wait_for_customer_search_to_settle(self) -> None:
        self.wait_for_kendo_combobox_search_to_settle(
            "//kendo-combobox[@name='choose_type_value']//input"
            " | //label[@name='choose_type']/following::kendo-combobox[1]//input"
        )

    def _select_walk_in_customer(self, data: Mapping[str, str]) -> dict[str, Any]:
        primary_customer_name = (
            str(data.get("contact_person") or "walk-in").strip() or "walk-in"
        )
        fallback_customer_name = "walk-in"
        self._debug(f"Typing customer search: {primary_customer_name}")
        self._wait_for_customer_search_to_settle()
        self._replace_customer_search_value(primary_customer_name)
        self._wait_for_customer_search_to_settle()

        self._debug(f"Selecting '{primary_customer_name}' from customer dropdown")
        selection_outcome = self._select_customer_dropdown_option(primary_customer_name)
        if selection_outcome != "selected":
            self._debug(
                f"Could not find '{primary_customer_name}' in dropdown, "
                f"using fallback customer value: {fallback_customer_name}"
            )
            self._replace_customer_search_value(fallback_customer_name)
            self._wait_for_customer_search_to_settle()
            self._debug("Selecting 'walk-in' from customer dropdown")
            self._select_walk_in_dropdown_option()
            self._wait_for_customer_search_to_settle()
            return {
                "used_fallback_customer": True,
                "requested_customer_name": primary_customer_name,
                "selected_customer_name": fallback_customer_name,
                "fallback_reason": selection_outcome,
            }
        self._wait_for_customer_search_to_settle()
        return {
            "used_fallback_customer": False,
            "requested_customer_name": primary_customer_name,
            "selected_customer_name": primary_customer_name,
            "fallback_reason": None,
        }

    def _replace_customer_search_value(self, value: str) -> None:
        locator = self._loc(self.CHOOSE_CUSTOMER_INPUT).first
        locator.wait_for(state="visible", timeout=self._timeout_ms)
        locator.click()
        locator.fill(value, timeout=self._timeout_ms)
        # Give the dropdown a moment to open and populate after typing
        self.page.wait_for_timeout(3500)
        # Trigger Angular/Kendo change detection if fill alone is not enough
        current = locator.input_value()
        if (current or "").strip() != (value or "").strip():
            self.page.evaluate(
                """([selector, newValue]) => {
                    const input = document.evaluate(
                      selector, document, null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE, null
                    ).singleNodeValue;
                    if (!input) return;
                    input.value = newValue;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'n' }));
                }""",
                [
                    "//kendo-combobox[@name='choose_type_value']//input"
                    " | //label[@name='choose_type']/following::kendo-combobox[1]//input",
                    value or "",
                ],
            )
            self.page.wait_for_timeout(3500)

    def _select_customer_dropdown_option(self, search_text: str) -> str:
        try:
            return self.page.wait_for_function(
                """(normalizedSearch) => {
                    const nodes = Array.from(document.querySelectorAll(
                      ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                    ));
                    const noDataNode = Array.from(document.querySelectorAll(
                      ".k-animation-container .k-nodata, .k-list .k-nodata, .k-no-data, .k-list-nodata"
                    )).find(node => {
                      const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                      return text.includes("no data found");
                    });
                    if (noDataNode) return "no_data_found";
                    if (nodes.length === 0) return false;
                    const target = nodes.find(node => {
                      const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                      return normalizedSearch && text === normalizedSearch;
                    });
                    if (target) {
                      target.scrollIntoView({ block: "center" });
                      target.click();
                      return "selected";
                    }
                    return "no_exact_match";
                }""",
                arg=search_text.strip().lower(),
                timeout=self._timeout_ms,
            ).json_value()
        except PlaywrightTimeoutError:
            return "timeout"

    def _select_walk_in_dropdown_option(self) -> None:
        self.page.wait_for_function(
            """() => {
                const nodes = Array.from(document.querySelectorAll(
                  ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                ));
                const target = nodes.find(node => {
                  const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                  return text.includes("walk-in") || text.includes("walk in");
                });
                if (!target) return false;
                target.scrollIntoView({ block: "center" });
                target.click();
                return true;
            }""",
            timeout=self._timeout_ms,
        )

    def _select_job_method(self, job_method: str) -> None:
        normalized_job_method = " ".join((job_method or "").split()).strip()
        if not normalized_job_method:
            normalized_job_method = "Digital Color"

        self._debug(f"Selecting job method: {normalized_job_method}")
        selected = self.page.evaluate(
            """(jobMethod) => {
                const normalize = value => (value || "")
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLowerCase();
                const target = normalize(jobMethod);
                const buttons = Array.from(
                    document.querySelectorAll("kendo-buttongroup[name='job_method_value'] button")
                );
                const button = buttons.find(node => {
                    const title = normalize(node.getAttribute("title"));
                    const label = normalize(
                        node.querySelector("label")?.innerText || node.innerText || node.textContent || ""
                    );
                    return title === target || label === target;
                });
                if (!button) return false;
                button.scrollIntoView({ block: "center" });
                button.click();
                return true;
            }""",
            normalized_job_method,
        )
        if not selected:
            raise ValueError(f"Unable to find job method button: {normalized_job_method}")
        self.wait_for_spinner_to_disappear()

        # Click Next only if the button is present and enabled
        next_loc = self._loc(self.NEXT_STEP_BUTTON)
        if next_loc.count() > 0 and next_loc.first.is_enabled():
            self._debug("Next button is enabled; clicking Next")
            next_loc.first.click()
            self.wait_for_spinner_to_disappear()

    def _wait_for_invoice_page(self) -> None:
        self._debug("Waiting for invoice page navigation")
        if self.INVOICE_PAGE_URL_PART in self.page.url:
            self._debug("Already on invoice page, skipping wait")
            return
        # Use wait_for_function instead of wait_for_url because Angular uses
        # hash-based routing (#/...) which does not trigger a real navigation
        # event — wait_for_url hangs indefinitely in headless/container mode.
        self.page.wait_for_function(
            """(urlPart) => window.location.href.includes(urlPart)""",
            arg=self.INVOICE_PAGE_URL_PART,
            timeout=self._timeout_ms,
        )
        self._debug(f"Invoice page URL confirmed: {self.page.url}")

    def _is_invoice_page(self) -> bool:
        return self.INVOICE_PAGE_URL_PART in (self.page.url or "")
