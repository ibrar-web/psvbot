import logging
from pathlib import Path

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimatePage(BasePage):
    CREATE_ESTIMATE_BUTTON = (
        "//div[contains(@class,'qa-access') and @name='menuitem_1'"
        " and .//span[contains(@class,'quick-access-item-text') and normalize-space()='Create Estimate']]"
    )
    CREATE_ESTIMATE_TEXT = (
        ".//span[contains(@class,'quick-access-item-text') and normalize-space()='Create Estimate']"
    )
    INVOICE_PAGE_URL_PART = "#/invoicing/invoice-page"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimatePage] {message}")
            logger.info(message)

    def click_create_estimate_quick_access(self) -> None:
        self._debug("Waiting for quick access page to finish loading")
        initial_url = self.driver.current_url
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        self._debug("Quick access document readyState is complete")

        self._debug("Waiting for Create Estimate card to be present")
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const cardXPath = arguments[0];
                    return !!document.evaluate(
                      cardXPath,
                      document,
                      null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE,
                      null
                    ).singleNodeValue;
                    """,
                    self.CREATE_ESTIMATE_BUTTON,
                )
            )
        )

        for attempt in range(1, 5):
            self._debug(f"Create Estimate click attempt {attempt}/4")
            try:
                # No stored WebElement refs: always resolve node inside JS to avoid stale errors.
                click_result = self.driver.execute_script(
                    """
                    const cardXPath = arguments[0];
                    const textXPath = arguments[1];
                    const getNode = (xp, root) => document.evaluate(
                      xp,
                      root || document,
                      null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE,
                      null
                    ).singleNodeValue;

                    const card = getNode(cardXPath);
                    if (!card) return { clicked: false, reason: "not_found" };

                    card.scrollIntoView({ block: "center" });
                    let textNode = getNode(textXPath, card);

                    try {
                      if (textNode) {
                        textNode.click();
                      }
                    } catch (e) {}

                    return {
                      clicked: true,
                      html: (card.outerHTML || "").slice(0, 400)
                    };
                    """,
                    self.CREATE_ESTIMATE_BUTTON,
                    self.CREATE_ESTIMATE_TEXT,
                )
                if attempt == 1:
                    self._debug(f"Create Estimate target html: {(click_result or {}).get('html', '')}")
                self._debug(f"Click dispatch result: {click_result}")
            except StaleElementReferenceException:
                self._debug("Create Estimate element went stale; retrying with fresh reference.")
                continue

            self.wait_for_spinner_to_disappear()
            if self._wait_for_invoice_page(4):
                self._debug(f"Create Estimate opened invoice page. URL: {self.driver.current_url}")
                return

            self._debug(f"No navigation after attempt {attempt}. URL: {self.driver.current_url}")

        screenshot_dir = Path(__file__).resolve().parents[1] / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / "create_estimate_failure.png"
        try:
            self.driver.save_screenshot(str(screenshot_path))
            self._debug(f"Create Estimate failure screenshot: {screenshot_path}")
        except Exception:
            pass

        raise TimeoutException(
            "Create Estimate clicked but did not open invoice page. "
            f"Initial URL: {initial_url}, Current URL: {self.driver.current_url}"
        )

    def _wait_for_invoice_page(self, timeout_seconds: int) -> bool:
        try:
            WebDriverWait(self.driver, timeout_seconds).until(
                lambda d: self.INVOICE_PAGE_URL_PART in (d.current_url or "")
            )
            return True
        except TimeoutException:
            return False
