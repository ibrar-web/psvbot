import logging
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.config import DEBUG, HEADLESS
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
        self.wait_for_spinner_to_disappear()

        self._open_record_with_lock_retry(estimate_id)
        self.wait_for_spinner_to_disappear()

    def _open_record_with_lock_retry(self, estimate_id: str) -> None:
        # Spinner has already settled. Either we've already navigated to the
        # invoice/detail page, or PrintSmith is showing a "record is locked
        # by user X" alert and we're still on the grid URL.
        if self.INVOICE_PAGE_URL_FRAGMENT in self.page.url:
            return

        if self.dismiss_locked_record_dialog_if_present():
            self._debug(f"Estimate {estimate_id} is locked; releasing all record locks and retrying")
            self.unlock_all_records()
            link_loc = self._loc(self.ESTIMATE_NUMBER_CELL_LINK).first
            link_loc.wait_for(state="visible", timeout=self._timeout_ms)
            link_loc.click()
            self.wait_for_spinner_to_disappear()

        # Full-budget wait as a safety net: covers a slow-but-successful
        # first navigation (spinner cleared before the route actually
        # changed) and confirms the post-unlock retry actually landed.
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
        temp_dir = Path(tempfile.mkdtemp(prefix="psv_estimate_detail_"))
        if HEADLESS:
            return self._download_details_headless(temp_dir)
        return self._download_details_headed(temp_dir)

    def _download_details_headless(self, temp_dir: Path) -> Path:
        download_timeout = max(self._timeout_ms, 120_000)
        with self.page.expect_download(timeout=download_timeout) as download_info:
            self.click(self.DETAIL_DOWNLOAD_BUTTON)
            self._debug("Download estimate details clicked; waiting for download")

        download = download_info.value
        suggested = download.suggested_filename or f"estimate_detail_{int(time.time())}.pdf"
        filename = self._sanitize_filename(suggested, default_extension="pdf")
        target_path = self._unique_path(temp_dir / filename)

        download.save_as(target_path)
        self._debug(f"Estimate detail downloaded to: {target_path}")

        failure = download.failure()
        if failure:
            raise RuntimeError(f"Download failed: {failure}")

        return target_path

    def _download_details_headed(self, temp_dir: Path) -> Path:
        # In headed mode Chromium opens the PDF in a new tab via window.open()
        # instead of firing a Playwright download event. Same pattern already
        # proven in EstimatedSummaryTab (create-estimate invoice download):
        # wait for that tab, grab the URL, fetch it manually via cookies, then
        # close the tab so the flow returns to the main page.
        with self.page.context.expect_page(timeout=max(self._timeout_ms, 120_000)) as new_page_info:
            self.click(self.DETAIL_DOWNLOAD_BUTTON)
            self._debug("Download estimate details clicked; waiting for generated document tab")

        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=max(self._timeout_ms, 120_000))

        try:
            download_url = self._wait_for_detail_download_url(new_page)
            self._debug(f"Resolved estimate detail download URL: {download_url}")
            cookies = self.page.context.cookies()
            saved_path = self._download_detail_file(download_url, temp_dir, cookies, new_page)
            self._debug(f"Estimate detail downloaded to: {saved_path}")
            return saved_path
        finally:
            try:
                new_page.wait_for_timeout(5000)
                new_page.close()
                self._debug("Closed generated document tab; returning to main page")
            except Exception:
                pass

    def _wait_for_detail_download_url(self, new_page) -> str:
        def resolve_url():
            url = (new_page.url or "").strip()
            if url.startswith(("http://", "https://")):
                return url
            return new_page.evaluate(
                """() => {
                    const candidates = [
                      document.querySelector("embed[type='application/pdf']")?.src,
                      document.querySelector("iframe")?.src,
                      document.querySelector("object")?.data,
                      ...performance.getEntriesByType("resource").map(entry => entry.name),
                    ].filter(Boolean);
                    return candidates.find(value => /^https?:/i.test(value)) || null;
                }"""
            )

        deadline = time.monotonic() + max(self.timeout, 120)
        while time.monotonic() < deadline:
            try:
                url = resolve_url()
                if url:
                    return url
            except Exception:
                pass
            new_page.wait_for_timeout(500)

        raise PlaywrightTimeoutError("Unable to resolve generated estimate detail download URL")

    def _download_detail_file(self, url: str, target_dir: Path, cookies: list, new_page) -> Path:
        cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies if c.get("name")
        )
        user_agent = new_page.evaluate("() => navigator.userAgent")

        request = Request(
            url,
            headers={
                "Cookie": cookie_header,
                "User-Agent": user_agent,
                "Referer": new_page.url,
            },
        )

        with urlopen(request, timeout=max(self.timeout, 120)) as response:
            filename = self._build_detail_filename(url, response.headers.get("Content-Disposition", ""))
            target_path = self._unique_path(target_dir / filename)
            with target_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        return target_path

    def _build_detail_filename(self, url: str, content_disposition: str) -> str:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition or "", re.I)
        if match:
            filename = unquote(match.group(1).strip())
        else:
            filename = Path(urlparse(url).path).name or f"estimate_detail_{int(time.time())}.pdf"

        filename = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
            or f"estimate_detail_{int(time.time())}.pdf"
        )
        if "." not in filename:
            filename = f"{filename}.pdf"
        return filename

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        timestamp = int(time.time())
        return path.with_name(f"{stem}_{timestamp}{suffix}")
