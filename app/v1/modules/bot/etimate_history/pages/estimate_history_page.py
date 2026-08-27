import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimateHistoryPage(BasePage):
    """Shared behavior for the Estimate History screen: opening it from
    quick-access and clearing its filters. Export-specific (CSV download) and
    lookup-specific (search + open one record) actions live in the subclasses
    EstimateHistoryExportPage / EstimateHistoryLookupPage.
    """

    ESTIMATE_HISTORY_MENU_ITEM = (
        "xpath=//div[contains(@class,'qa-access') and @name='menuitem_14'"
        " and .//span[contains(@class,'quick-access-item-text') and contains(normalize-space(),'Estimate History')]]"
    )
    ESTIMATE_HISTORY_MENU_ITEM_TEXT = (
        ".//span[contains(@class,'quick-access-item-text') and contains(normalize-space(),'Estimate History')]"
    )
    # Both present in the grid toolbar regardless of which action follows.
    # DOWNLOAD_CSV_BUTTON is the proven-live "grid finished loading" readiness
    # signal (verified across both the export and lookup flows already).
    DOWNLOAD_CSV_BUTTON = "xpath=//a[@name='downloadAsCSVButton']"
    CLEAR_FILTERS_BUTTON = "xpath=//a[@name='reset_estimate_history_grid']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateHistoryPage] {message}")
        logger.info(message)

    def open_from_quick_access(self) -> None:
        self._debug("Waiting for quick access page to finish loading")
        initial_url = self.page.url
        self.page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        self._debug("Quick access document ready")

        self._debug("Waiting for Estimate History card to be present and visible")
        self.page.wait_for_function(
            """(cardXPath) => {
                const node = document.evaluate(
                  cardXPath, document, null,
                  XPathResult.FIRST_ORDERED_NODE_TYPE, null
                ).singleNodeValue;
                if (!node) return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }""",
            arg=self.ESTIMATE_HISTORY_MENU_ITEM.replace("xpath=", ""),
            timeout=self._timeout_ms,
        )
        # Give Angular time to bind event listeners after the node is visible
        self.page.wait_for_timeout(500)

        for attempt in range(1, 5):
            self._debug(f"Estimate History click attempt {attempt}/4")

            # If Angular already navigated away (card gone = navigation in progress),
            # just wait for the grid instead of trying to click again.
            if self.is_visible(self.DOWNLOAD_CSV_BUTTON):
                self._debug(f"Already on Estimate History page at attempt {attempt}. URL: {self.page.url}")
                return

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
                    self.ESTIMATE_HISTORY_MENU_ITEM.replace("xpath=", ""),
                    self.ESTIMATE_HISTORY_MENU_ITEM_TEXT,
                ],
            )
            if attempt == 1:
                self._debug(f"Estimate History target html: {(click_result or {}).get('html', '')}")
            self._debug(f"Click dispatch result: {click_result}")

            # Card not found means Angular already navigated — wait longer for the grid to settle
            timeout = 10 if not (click_result or {}).get("clicked") else 4
            self.wait_for_spinner_to_disappear()
            if self._wait_for_history_grid(timeout):
                self._debug(f"Estimate History grid opened. URL: {self.page.url}")
                return

            self._debug(f"No navigation after attempt {attempt}. URL: {self.page.url}")

        # Screenshot on failure
        screenshot_dir = Path(__file__).resolve().parents[2] / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / "estimate_history_failure.png"
        try:
            self.page.screenshot(path=str(screenshot_path))
            self._debug(f"Estimate History failure screenshot: {screenshot_path}")
        except Exception:
            pass

        raise PlaywrightTimeoutError(
            f"Estimate History clicked but the grid (Download as CSV button) did not appear. "
            f"Initial URL: {initial_url}, Current URL: {self.page.url}"
        )

    def _wait_for_history_grid(self, timeout_seconds: int) -> bool:
        if self.is_visible(self.DOWNLOAD_CSV_BUTTON):
            return True
        try:
            self._loc(self.DOWNLOAD_CSV_BUTTON).first.wait_for(
                state="visible", timeout=timeout_seconds * 1000
            )
            return True
        except PlaywrightTimeoutError:
            return False

    def clear_filters(self) -> None:
        self._debug("Clearing all Estimate History grid filters")
        self.wait_for_spinner_to_disappear()
        self.click(self.CLEAR_FILTERS_BUTTON)
        self.wait_for_spinner_to_disappear()

    def _sanitize_filename(self, filename: str, default_extension: str = "") -> str:
        filename = unquote(filename)
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
        if not filename:
            filename = f"estimate_history_{int(time.time())}"
        if "." not in filename and default_extension:
            filename = f"{filename}.{default_extension.lstrip('.')}"
        return filename
