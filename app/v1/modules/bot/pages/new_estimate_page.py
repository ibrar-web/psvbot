import logging
from collections.abc import Mapping

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class NewEstimatePage(BasePage):
    INVOICE_PAGE_URL_PART = "#/invoicing/invoice-page"
    CHOOSE_CUSTOMER_LABEL = (
        "//label[@name='choose_type' and normalize-space()='Choose Customer']"
    )
    CHOOSE_CUSTOMER_INPUT = (
        "//kendo-combobox[@name='choose_type_value']//input"
        " | //label[@name='choose_type']/following::kendo-combobox[1]//input"
    )
    DIGITAL_COLOR_BUTTON = (
        "//kendo-buttongroup[@name='job_method_value']//button["
        "@title='Digital Color' or .//label[normalize-space()='Digital Color']"
        "]"
    )
    NEXT_STEP_BUTTON = "//button[@name='next_step_button']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][NewEstimatePage] {message}")
            logger.info(message)

    def complete_walk_in_digital_color(
        self,
        data: Mapping[str, str] | None = None,
    ) -> None:
        self._wait_for_modal_ready()
        self._select_walk_in_customer(data or {})
        self._select_digital_color()
        self._wait_for_invoice_page()

    def _wait_for_modal_ready(self) -> None:
        self._debug("Waiting for Choose Customer form on invoice page")
        self.wait_for_spinner_to_disappear()
        self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_LABEL)
        self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_INPUT)

    def _select_walk_in_customer(self, data: Mapping[str, str]) -> None:
        primary_customer_name = (
            str(data.get("contact_person") or "walk-in").strip() or "walk-in"
        )
        fallback_customer_name = "walk-in"
        self._debug(f"Typing customer search: {primary_customer_name}")
        self.wait_for_spinner_to_disappear()
        customer_input = self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_INPUT)
        self._replace_customer_search_value(customer_input, primary_customer_name)
        self.wait_for_spinner_to_disappear()

        self._debug(f"Selecting '{primary_customer_name}' from customer dropdown")
        try:
            self._select_customer_dropdown_option(primary_customer_name)
        except TimeoutException:
            self._debug(
                f"Could not find '{primary_customer_name}' in dropdown, "
                "using fallback customer value: "
                f"{fallback_customer_name}"
            )
            customer_input = self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_INPUT)
            self._replace_customer_search_value(customer_input, fallback_customer_name)
            self.wait_for_spinner_to_disappear()
            self._debug("Selecting 'walk-in' from customer dropdown")
            self._select_customer_dropdown_option(fallback_customer_name)
        self.wait_for_spinner_to_disappear()

    def _replace_customer_search_value(self, customer_input, value: str) -> None:
        self.driver.execute_script("arguments[0].click();", customer_input)
        customer_input.send_keys(Keys.CONTROL, "a")
        customer_input.send_keys(Keys.DELETE)
        if value:
            customer_input.send_keys(value)
        current_value = (customer_input.get_attribute("value") or "").strip()
        expected_value = (value or "").strip()
        if current_value != expected_value:
            self.driver.execute_script(
                """
                const input = arguments[0];
                const newValue = arguments[1];
                input.value = newValue;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'n' }));
                """,
                customer_input,
                value or "",
            )

    def _select_customer_dropdown_option(self, search_text: str) -> None:
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const normalizedSearch = (arguments[0] || "").trim().toLowerCase();
                    const nodes = Array.from(document.querySelectorAll(
                      ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                    ));
                    const target = nodes.find(node => {
                      const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                      return normalizedSearch && text.includes(normalizedSearch);
                    });
                    if (!target) return false;
                    target.scrollIntoView({block: "center"});
                    target.click();
                    return true;
                    """,
                    search_text,
                )
            )
        )

    def _select_digital_color(self) -> None:
        self._debug("Selecting job method: Digital Color")
        self.click(By.XPATH, self.DIGITAL_COLOR_BUTTON)
        self.wait_for_spinner_to_disappear()

        # Some screens require Next after method selection; click only if enabled.
        next_buttons = self.driver.find_elements(By.XPATH, self.NEXT_STEP_BUTTON)
        if next_buttons and next_buttons[0].is_enabled():
            self._debug("Next button is enabled; clicking Next")
            next_buttons[0].click()
            self.wait_for_spinner_to_disappear()

    def _wait_for_invoice_page(self) -> None:
        self._debug("Waiting for invoice page navigation")
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: self._is_invoice_page()
        )

    def _is_invoice_page(self) -> bool:
        return self.INVOICE_PAGE_URL_PART in (self.driver.current_url or "")
