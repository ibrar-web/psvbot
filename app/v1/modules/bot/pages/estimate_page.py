import logging
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimatePage(BasePage):
    CREATE_ESTIMATE_BUTTON = (
        "xpath=//div[contains(@class,'qa-access') and @name='menuitem_1'"
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
        initial_url = self.page.url
        self.page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        self._debug("Quick access document ready")

        self._debug("Waiting for Create Estimate card to be present")
        self.page.wait_for_function(
            """(cardXPath) => {
                return !!document.evaluate(
                  cardXPath, document, null,
                  XPathResult.FIRST_ORDERED_NODE_TYPE, null
                ).singleNodeValue;
            }""",
            arg=self.CREATE_ESTIMATE_BUTTON.replace("xpath=", ""),
            timeout=self._timeout_ms,
        )

        for attempt in range(1, 5):
            self._debug(f"Create Estimate click attempt {attempt}/4")
            click_result = self.page.evaluate(
                """([cardXPath, textXPath]) => {
                    const getNode = (xp, root) => document.evaluate(
                      xp, root || document, null,
                      XPathResult.FIRST_ORDERED_NODE_TYPE, null
                    ).singleNodeValue;

                    const card = getNode(cardXPath);
                    if (!card) return { clicked: false, reason: "not_found" };

                    card.scrollIntoView({ block: "center" });
                    let textNode = getNode(textXPath, card);
                    try {
                      if (textNode) { textNode.click(); }
                    } catch (e) {}

                    return {
                      clicked: true,
                      html: (card.outerHTML || "").slice(0, 400)
                    };
                }""",
                [
                    self.CREATE_ESTIMATE_BUTTON.replace("xpath=", ""),
                    self.CREATE_ESTIMATE_TEXT,
                ],
            )
            if attempt == 1:
                self._debug(f"Create Estimate target html: {(click_result or {}).get('html', '')}")
            self._debug(f"Click dispatch result: {click_result}")

            self.wait_for_spinner_to_disappear()
            if self._wait_for_invoice_page(4):
                self._debug(f"Create Estimate opened invoice page. URL: {self.page.url}")
                return

            self._debug(f"No navigation after attempt {attempt}. URL: {self.page.url}")

        # Screenshot on failure
        screenshot_dir = Path(__file__).resolve().parents[1] / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / "create_estimate_failure.png"
        try:
            self.page.screenshot(path=str(screenshot_path))
            self._debug(f"Create Estimate failure screenshot: {screenshot_path}")
        except Exception:
            pass

        raise PlaywrightTimeoutError(
            f"Create Estimate clicked but did not open invoice page. "
            f"Initial URL: {initial_url}, Current URL: {self.page.url}"
        )

    def _wait_for_invoice_page(self, timeout_seconds: int) -> bool:
        try:
            self.page.wait_for_url(
                f"**{self.INVOICE_PAGE_URL_PART}**",
                timeout=timeout_seconds * 1000,
            )
            return True
        except PlaywrightTimeoutError:
            return False
