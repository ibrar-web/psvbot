
import logging

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class NewEstimatePage(BasePage):
    INVOICE_PAGE_URL_PART = "#/invoicing/invoice-page"
    CHOOSE_CUSTOMER_LABEL = "//label[@name='choose_type' and normalize-space()='Choose Customer']"
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

    def complete_walk_in_digital_color(self) -> None:
        self._wait_for_modal_ready()
        self._select_walk_in_customer()
        self._select_digital_color()
        self._wait_for_invoice_page()

    def _wait_for_modal_ready(self) -> None:
        self._debug("Waiting for Choose Customer form on invoice page")
        self.wait_for_spinner_to_disappear()
        self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_LABEL)
        self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_INPUT)

    def _select_walk_in_customer(self) -> None:
        self._debug("Typing customer search: walk-in")
        self.wait_for_spinner_to_disappear()
        customer_input = self.wait_for_visible(By.XPATH, self.CHOOSE_CUSTOMER_INPUT)
        self.driver.execute_script("arguments[0].click();", customer_input)
        customer_input.clear()
        customer_input.send_keys("walk-in")
        self.wait_for_spinner_to_disappear()

        self._debug("Selecting 'walk-in' from customer dropdown")
        selected = WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const nodes = Array.from(document.querySelectorAll(
                      ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                    ));
                    const target = nodes.find(node => {
                      const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                      return text.includes("walk-in") || text.includes("walk in");
                    });
                    if (!target) return false;
                    target.scrollIntoView({block: "center"});
                    target.click();
                    return true;
                    """
                )
            )
        )
        if not selected:
            raise TimeoutException("Could not find 'walk-in' option in customer dropdown")
        self.wait_for_spinner_to_disappear()

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
        WebDriverWait(self.driver, self.timeout).until(lambda d: self._is_invoice_page())

    def _is_invoice_page(self) -> bool:
        return self.INVOICE_PAGE_URL_PART in (self.driver.current_url or "")
