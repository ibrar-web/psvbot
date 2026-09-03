import logging
import tempfile
import time
from pathlib import Path

from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.etimate_history.pages.estimate_history_page import EstimateHistoryPage

logger = logging.getLogger(__name__)


class EstimateHistoryExportPage(EstimateHistoryPage):
    """Estimate History screen, export (bulk CSV download) actions."""

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateHistoryExportPage] {message}")
        logger.info(message)

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
