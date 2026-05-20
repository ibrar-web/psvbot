import logging

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimateSelectionPage(BasePage):
    """Page for searching and selecting an existing estimate on the quick-access page."""

    # The category label/button that opens the module dropdown (shows current selection e.g. "Invoice")
    MODULE_CATEGORIES_DIV = "xpath=//div[contains(@class,'dot-search-categories')]"
    # The hidden <select> inside the categories div
    MODULE_SELECT = "xpath=//select[@name='module_select' or @id='module_select']"
    SEARCH_INPUT = "xpath=//input[@name='module_search_feild']"
    SEARCH_BUTTON = "xpath=//button[@name='search_button']"
    SEARCH_RESULTS = "xpath=//div[contains(@class,'search-results')]//a[contains(@class,'search-item')]"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateSelection] {message}")
        logger.info(message)

    def _select_estimate_module(self) -> None:
        """Click the module category button and select 'Estimate' (value=2) from the dropdown."""
        self._debug("Selecting 'Estimate' from module dropdown")

        # Click the categories div to reveal/activate the select
        self.wait_for_visible(self.MODULE_CATEGORIES_DIV)
        self.click(self.MODULE_CATEGORIES_DIV)

        # Use JS to set the select value to 2 (Estimate) and fire change event
        self.page.evaluate(
            """() => {
                const sel = document.querySelector(
                    'select[name="module_select"], select#module_select'
                );
                if (!sel) return;
                sel.value = '2';
                sel.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        self.wait_for_spinner_to_disappear()

        # Verify the label now shows "Estimate"
        label_text = self.page.evaluate(
            """() => {
                const label = document.querySelector(
                    'span[name="selected_module_label"]'
                );
                return label ? (label.textContent || '').trim() : '';
            }"""
        )
        self._debug(f"Module label after selection: '{label_text}'")

    def search_and_open_estimate(self, estimate_id: str) -> None:
        """Search for an existing estimate by ID and open it.

        Steps:
        1. Click the module category button and select 'Estimate' from the dropdown
        2. Type the estimate_id into the search field
        3. Click the search button and wait for spinner
        4. Find the result that exactly matches the estimate_id in the <b> tag
        5. Click it — this opens the estimate record and navigates to Estimate Summary
        """
        self._debug(f"Searching for existing estimate_id={estimate_id}")
        self.wait_for_spinner_to_disappear()

        # Step 1: Select 'Estimate' from the module dropdown
        self._select_estimate_module()

        # Step 2: Type the estimate_id into the search input
        self.wait_for_visible(self.SEARCH_INPUT)
        self.type(self.SEARCH_INPUT, str(estimate_id), clear_first=True)
        self._debug(f"Entered estimate_id '{estimate_id}' in search field")

        # Step 3: Click search button and wait for spinner
        self.click(self.SEARCH_BUTTON)
        self.wait_for_spinner_to_disappear()
        self._debug("Search triggered, waiting for results")

        # Step 4: Wait for search results to appear
        self.page.wait_for_function(
            """() => {
                const results = document.querySelectorAll(
                    "div.search-results a.search-item"
                );
                return results.length > 0;
            }""",
            timeout=self._timeout_ms,
        )
        self.wait_for_spinner_to_disappear()
        print(f"estimate_id: {estimate_id}")
        # Step 5: Find and click the result whose <b> tag contains the estimate_id digits.
        # The result text looks like: "[ Estimate:28799 ] - Mini Van Wrap..."
        # We normalise both sides to strings of digits to handle int/str/whitespace differences.
        clicked = self.page.evaluate(
            """(estimateId) => {
                const needle = String(estimateId).replace(/\\D/g, '').trim();
                if (!needle) return false;

                const items = document.querySelectorAll("div.search-results a.search-item");

                for (const item of items) {
                    const text = item.innerText.replace(/\\s+/g, ' ').trim();

                    // Extract full estimate number (works even if split across <b>)
                    const match = text.match(/Estimate:\\s*(\\d+)/i);

                    if (match && match[1] === needle) {
                        item.click();
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
