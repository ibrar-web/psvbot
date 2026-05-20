import logging

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimateSelectionPage(BasePage):
    """Page for searching and selecting an existing estimate on the quick-access page."""

    SEARCH_INPUT = "xpath=//input[@name='module_search_feild']"
    SEARCH_BUTTON = "xpath=//button[@name='search_button']"
    SEARCH_RESULTS = "xpath=//div[contains(@class,'search-results')]//a[contains(@class,'search-item')]"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateSelection] {message}")
        logger.info(message)

    def search_and_open_estimate(self, estimate_id: str) -> None:
        """Search for an existing estimate by ID and open it.

        Steps:
        1. Ensure the search input is visible
        2. Type the estimate_id into the search field
        3. Press Enter (or click search button) and wait for spinner
        4. Find the result that exactly matches the estimate_id
        5. Click it — this opens the estimate record and navigates to Estimate Summary
        """
        self._debug(f"Searching for existing estimate_id={estimate_id}")
        self.wait_for_spinner_to_disappear()

        # Type the estimate_id into the search input
        self.wait_for_visible(self.SEARCH_INPUT)
        self.type(self.SEARCH_INPUT, str(estimate_id), clear_first=True)
        self._debug(f"Entered estimate_id '{estimate_id}' in search field")

        # Click search button or press Enter to trigger search
        self.click(self.SEARCH_BUTTON)
        self.wait_for_spinner_to_disappear()
        self._debug("Search triggered, waiting for results")

        # Wait for search results to appear
        self.page.wait_for_function(
            """() => {
                const results = document.querySelectorAll(
                    "div.search-results a.search-item"
                );
                return results.length > 0;
            }""",
            timeout=self._timeout_ms,
        )

        # Find and click the result that exactly matches the estimate_id
        # The result text looks like: "[ Estimate:28799 ] - Mini Van Wrap..."
        # We match the <b> tag content against the estimate_id
        clicked = self.page.evaluate(
            """(estimateId) => {
                const results = document.querySelectorAll(
                    "div.search-results a.search-item"
                );
                for (const result of results) {
                    const boldTag = result.querySelector("b");
                    if (boldTag && boldTag.textContent.trim() === String(estimateId)) {
                        result.click();
                        return true;
                    }
                }
                return false;
            }""",
            str(estimate_id),
        )

        if not clicked:
            raise RuntimeError(
                f"No search result found matching estimate_id={estimate_id}"
            )

        self._debug(f"Clicked search result for estimate_id={estimate_id}")
        self.wait_for_spinner_to_disappear()
        self._debug("Estimate record opened, spinner dismissed")
