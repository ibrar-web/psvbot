import logging
import re
import tempfile
import time
from pathlib import Path

from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.etimate_history.pages.estimate_history_page import EstimateHistoryPage

logger = logging.getLogger(__name__)


class EstimateHistoryExportPage(EstimateHistoryPage):
    """Estimate History screen, export (bulk CSV download) actions."""

    # Status column filter; shows "Show All" after clear_filters().
    STATUS_FILTER_DROPDOWN = "xpath=//kendo-dropdownlist[@name='filter_status_dropdown']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateHistoryExportPage] {message}")
        logger.info(message)

    def apply_won_filter(self) -> None:
        self._select_status_filter("Won")

    def _select_status_filter(self, option_text: str) -> None:
        self._debug(f"Setting Estimate History Status filter to '{option_text}'")
        self.wait_for_spinner_to_disappear()
        dropdown_loc = self._loc(self.STATUS_FILTER_DROPDOWN).first
        dropdown_loc.wait_for(state="visible", timeout=self._timeout_ms)
        dropdown_loc.scroll_into_view_if_needed(timeout=self._timeout_ms)
        dropdown_loc.locator(".k-dropdown-wrap").first.click(timeout=self._timeout_ms)

        # Scope to the popup this dropdown opened via its aria-owns id —
        # same approach as BasePage._select_record_lock_scope_all_users, so
        # other Kendo popups on the page can't be matched by mistake.
        popup_id = dropdown_loc.locator("[aria-owns]").first.get_attribute(
            "aria-owns", timeout=self._timeout_ms
        )
        option_loc = self._loc(f"css=[id='{popup_id}'] li").filter(
            has_text=re.compile(rf"^\s*{re.escape(option_text)}\s*$")
        ).first
        option_loc.wait_for(state="visible", timeout=self._timeout_ms)
        option_loc.click(timeout=self._timeout_ms)

        # Same spinner race as clear_filters(): the grid reload can take a
        # couple of seconds to start after the selection.
        self.page.wait_for_timeout(3000)
        self.wait_for_spinner_to_disappear()

        selected = (dropdown_loc.locator(".k-input").first.inner_text() or "").strip()
        if selected != option_text:
            raise RuntimeError(
                f"Status filter shows '{selected}' after selecting '{option_text}'"
            )
        self._nudge_mouse()

    def download_csv(self) -> Path:
        download_timeout = max(self._timeout_ms, 120_000)
        self._nudge_mouse()
        with self.page.expect_download(timeout=download_timeout) as download_info:
            self.click(self.DOWNLOAD_CSV_BUTTON)
            self._debug("Download as CSV clicked; waiting for download")

        download = download_info.value
        suggested = download.suggested_filename or f"estimate_history_{int(time.time())}.csv"
        filename = self._sanitize_filename(suggested, default_extension="csv")
        temp_dir = Path(tempfile.mkdtemp(prefix="psv_estimate_history_"))
        target_path = temp_dir / filename

        download.save_as(target_path)
        self._debug(f"Estimate history CSV downloaded to: {target_path}")

        failure = download.failure()
        if failure:
            raise RuntimeError(f"Download failed: {failure}")

        return target_path
