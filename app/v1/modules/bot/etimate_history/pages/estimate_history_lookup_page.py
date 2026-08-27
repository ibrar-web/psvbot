import logging

from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.etimate_history.pages.estimate_history_page import EstimateHistoryPage

logger = logging.getLogger(__name__)


class EstimateHistoryLookupPage(EstimateHistoryPage):
    """Estimate History screen, single-record lookup actions."""

    ESTIMATE_NUMBER_FILTER_INPUT = "xpath=//input[@name='filter_invoiceNumber_input']"
    # The "Estimate #" cell itself (not the <tr>, which is not clickable —
    # clicking the row alone does not navigate). Verified live: this is the
    # element PrintSmith actually wires a click handler to.
    ESTIMATE_NUMBER_CELL_LINK = "xpath=//a[contains(@class,'acc_info_celldata')]"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateHistoryLookupPage] {message}")
        logger.info(message)

    def search_by_estimate_id(self, estimate_id: str) -> None:
        self._debug(f"Filtering Estimate History grid by Estimate #: {estimate_id}")
        self.wait_for_spinner_to_disappear()
        filter_loc = self._loc(self.ESTIMATE_NUMBER_FILTER_INPUT).first
        filter_loc.wait_for(state="visible", timeout=self._timeout_ms)
        filter_loc.click()
        filter_loc.fill(str(estimate_id))
        filter_loc.press("Enter")
        self.wait_for_spinner_to_disappear()

    def open_first_search_result(self, estimate_id: str = "") -> None:
        self._debug("Waiting for filtered Estimate History row")
        self.wait_for_spinner_to_disappear()
        link_loc = self._loc(self.ESTIMATE_NUMBER_CELL_LINK).first
        link_loc.wait_for(state="visible", timeout=self._timeout_ms)
        link_loc.click()
        if estimate_id:
            # Confirm we actually navigated to this record, not just clicked.
            self.page.wait_for_function(
                """(id) => window.location.href.includes(id)""",
                arg=str(estimate_id),
                timeout=self._timeout_ms,
            )
        self.wait_for_spinner_to_disappear()

    def search_and_open_by_estimate_id(self, estimate_id: str) -> None:
        # Clear any filters left over from a previous session/state (e.g. a
        # stray Accounting filter value) before applying our own, otherwise
        # the Estimate # match can get filtered out entirely.
        self.clear_filters()
        self.search_by_estimate_id(estimate_id)
        self.open_first_search_result(estimate_id)
        self._debug(f"Opened Estimate History record for estimate_id={estimate_id}. URL: {self.page.url}")
