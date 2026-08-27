import logging
import tempfile
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
    # Same print_btn_group/print_btn component already proven to work for the
    # invoice PDF download in EstimatedSummaryTab (create-estimate flow) —
    # this detail page reuses the identical "US685 E-Estimate" print button.
    DETAIL_DOWNLOAD_BUTTON = (
        "xpath=//div[@name='print_btn_group']//button[@name='print_btn'"
        " and .//span[normalize-space()='US685 E-Estimate']]"
    )

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

    # Confirmed exact routes: clicking a record on the grid either (a) stays
    # on the grid URL ("#/history/estimatehistory") and shows the "locked by
    # user" dialog, or (b) navigates to INVOICE_PAGE_URL_FRAGMENT. Using
    # Playwright's native page.wait_for_url() — tracked at the driver/CDP
    # level, not evaluated as JS inside the page — so it survives the
    # transient reload that made wait_for_function throw "Target page,
    # context or browser has been closed".
    INVOICE_PAGE_URL_FRAGMENT = "#/invoicing/invoice-page"

    def open_first_search_result(self, estimate_id: str = "") -> None:
        self._debug("Waiting for filtered Estimate History row")
        self.wait_for_spinner_to_disappear()
        link_loc = self._loc(self.ESTIMATE_NUMBER_CELL_LINK).first
        link_loc.wait_for(state="visible", timeout=self._timeout_ms)
        link_loc.click()

        self._open_record_with_lock_retry(estimate_id)
        self.wait_for_spinner_to_disappear()

    def _open_record_with_lock_retry(self, estimate_id: str) -> None:
        # Short first attempt: if the record is locked, PrintSmith shows an
        # error dialog instead of navigating, so we're still on the grid URL.
        if self._wait_for_invoice_page(timeout_ms=15_000, raise_on_timeout=False):
            return

        if self.dismiss_locked_record_dialog_if_present():
            self._debug(f"Estimate {estimate_id} is locked; releasing all record locks and retrying")
            self.unlock_all_records()
            link_loc = self._loc(self.ESTIMATE_NUMBER_CELL_LINK).first
            link_loc.wait_for(state="visible", timeout=self._timeout_ms)
            link_loc.click()

        # Full-budget wait for the real thing: either it was never locked and
        # is just slow, or we just released the lock and are retrying.
        self._wait_for_invoice_page(timeout_ms=self._timeout_ms, raise_on_timeout=True)

    def _wait_for_invoice_page(self, *, timeout_ms: int, raise_on_timeout: bool) -> bool:
        try:
            self.page.wait_for_url(
                lambda url: self.INVOICE_PAGE_URL_FRAGMENT in url,
                timeout=timeout_ms,
            )
            return True
        except PlaywrightTimeoutError:
            if raise_on_timeout:
                raise
            return False

    def search_and_open_by_estimate_id(self, estimate_id: str) -> None:
        # Clear any filters left over from a previous session/state (e.g. a
        # stray Accounting filter value) before applying our own, otherwise
        # the Estimate # match can get filtered out entirely.
        self.clear_filters()
        self.search_by_estimate_id(estimate_id)
        self.open_first_search_result(estimate_id)
        self._debug(f"Opened Estimate History record for estimate_id={estimate_id}. URL: {self.page.url}")

    def download_details(self) -> Path:
        download_timeout = max(self._timeout_ms, 120_000)
        with self.page.expect_download(timeout=download_timeout) as download_info:
            self.click(self.DETAIL_DOWNLOAD_BUTTON)
            self._debug("Download estimate details clicked; waiting for download")

        download = download_info.value
        suggested = download.suggested_filename or f"estimate_detail_{int(time.time())}.pdf"
        filename = self._sanitize_filename(suggested, default_extension="pdf")
        temp_dir = Path(tempfile.mkdtemp(prefix="psv_estimate_detail_"))
        target_path = temp_dir / filename

        download.save_as(target_path)
        self._debug(f"Estimate detail downloaded to: {target_path}")

        failure = download.failure()
        if failure:
            raise RuntimeError(f"Download failed: {failure}")

        return target_path
