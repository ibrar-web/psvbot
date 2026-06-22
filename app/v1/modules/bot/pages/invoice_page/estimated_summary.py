import logging
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import (
    DEBUG,
    HEADLESS,
    WANTED_DATE_DEFAULT_WORKING_DAYS,
)

logger = logging.getLogger(__name__)


class EstimatedSummaryTab(BasePage):
    ESTIMATE_SUMMARY_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Estimate Summary']]"
    ADD_BUTTON = "xpath=//div[@name='add_btn_group']//button"
    ADD_JOB_BUTTON = "xpath=//a[@name='add_job_btn']"
    CREATE_PROSPECT_BUTTON = (
        "xpath=//button[@name='create_account_button'"
        " and .//span[contains(normalize-space(),'Create Prospect')]]"
    )
    CREATE_PROSPECT_LINK = "xpath=//a[@name='create_account_button' and .//span[normalize-space()='Create Prospect']]"
    THREE_DOTS_BUTTON = "xpath=//div[contains(@class,'dot-more-options-icon')]"
    US685_E_ESTIMATE_BUTTON = (
        "xpath=//div[@name='print_btn_group']//button[@name='print_btn'"
        " and .//span[normalize-space()='US685 E-Estimate']]"
    )
    WANTED_DATE_INPUT = "xpath=//input[@name='wantedDate']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimatedSummaryTab] {message}")
        logger.info(message)

    def is_visible(self) -> bool:
        return super().is_visible(self.ESTIMATE_SUMMARY_TAB)

    def switch_to_tab(self) -> None:
        self.wait_for_spinner_to_disappear()
        self.click(self.ESTIMATE_SUMMARY_TAB)
        self.page.wait_for_function(
            """() => {
                const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                const target = tabs.find(t => (t.innerText || "").includes("Estimate Summary"));
                return !!target && target.getAttribute("aria-selected") === "true";
            }""",
            timeout=self._timeout_ms,
        )
        self.wait_for_spinner_to_disappear()

    def collect_estimate_totals(self) -> Dict[int, str]:
        """Read the collect-total input values from the Estimate Summary table.

        For each row in the tree-table body, extracts the value from the
        last <td> that contains an input[currency="true"] (the collect-total
        field).  Returns a dict mapping 1-based row index to the input value.
        """
        self.switch_to_tab()
        self.wait_for_spinner_to_disappear()

        totals: Dict[int, str] = self.page.evaluate(
            """() => {
                const tbody = document.querySelector('tbody.ui-treetable-tbody');
                if (!tbody) return {};
                const rows = tbody.querySelectorAll(':scope > tr');
                const result = {};
                rows.forEach((row, idx) => {
                    // Find the last <td> that contains an input with currency="true"
                    const tds = row.querySelectorAll(':scope > td');
                    for (let i = tds.length - 1; i >= 0; i--) {
                        const input = tds[i].querySelector('input[currency="true"]');
                        if (input) {
                            result[idx + 1] = (input.value || '').trim();
                            break;
                        }
                    }
                });
                return result;
            }"""
        )
        self._debug(f"Collected estimate totals: {totals}")
        return totals or {}

    def remove_all_items(self) -> None:
        """Remove all job items from the Estimate Summary table.

        Repeatedly clicks the first delete_item button, confirms "Yes" on
        the warning popup, and waits for the row to be removed. Continues
        until no items remain in the table.
        """
        self.switch_to_tab()
        self.wait_for_spinner_to_disappear()
        self._debug("Starting to remove all items from Estimate Summary")

        max_removals = 50  # safety limit to prevent infinite loops
        removed = 0

        for _ in range(max_removals):
            # Check if any delete_item button exists
            has_items = self.page.evaluate(
                """() => {
                    const btn = document.querySelector(
                        'tbody.ui-treetable-tbody span[name="delete_item"]'
                    );
                    return !!btn;
                }"""
            )
            if not has_items:
                self._debug(f"No more items to remove (total removed: {removed})")
                break

            # Click the first delete_item button using JS (it may be obscured)
            self.page.evaluate(
                """() => {
                    const btn = document.querySelector(
                        'tbody.ui-treetable-tbody span[name="delete_item"]'
                    );
                    if (btn) btn.click();
                }"""
            )

            # Wait for the confirmation dialog and click "Yes"
            self.page.wait_for_function(
                """() => {
                    const dialog = document.querySelector(
                        '.ui-confirmdialog.ui-dialog'
                    );
                    if (!dialog) return false;
                    const style = window.getComputedStyle(dialog);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && parseFloat(style.opacity || '1') > 0;
                }""",
                timeout=self._timeout_ms,
            )

            # Click the "Yes" button in the confirmation dialog
            self.page.evaluate(
                """() => {
                    const dialog = document.querySelector(
                        '.ui-confirmdialog.ui-dialog'
                    );
                    if (!dialog) return;
                    const yesBtn = Array.from(
                        dialog.querySelectorAll('button[pbutton] .ui-button-text')
                    ).find(b => (b.textContent || '').trim() === 'Yes');
                    if (yesBtn) yesBtn.click();
                }"""
            )
            removed += 1
            self._debug(f"Removed item #{removed}")

            # Wait for the dialog to close and spinner
            self.page.wait_for_function(
                """() => {
                    const dialog = document.querySelector(
                        '.ui-confirmdialog.ui-dialog'
                    );
                    if (!dialog) return true;
                    const style = window.getComputedStyle(dialog);
                    return style.display === 'none'
                        || style.visibility === 'hidden'
                        || parseFloat(style.opacity || '0') === 0;
                }""",
                timeout=self._timeout_ms,
            )
            self.wait_for_spinner_to_disappear()

        self._debug(f"Finished removing items. Total removed: {removed}")

    def click_us685_eestimate_and_download(
        self,
        customer_selection_status: Optional[Dict[str, Any]] = None,
    ) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="psv_invoices_"))
        customer_selection_status = customer_selection_status or {}

        if customer_selection_status.get("used_fallback_customer"):
            self._create_prospect()

        self._debug("Waiting for US685 E-Estimate button on Estimate Summary")

        if HEADLESS:
            return self._download_headless(temp_dir)
        else:
            return self._download_headed(temp_dir)



    def set_wanted_date(self, quote_record: Dict[str, Any]) -> str:
        """Fill the estimate's wanted (due) date field.

        Returns the normalized date string that was entered.
        """
        raw = self._extract_wanted_date(quote_record)
        wanted_date = self._normalize_wanted_date(raw)
        if not wanted_date:
            wanted_date = self._default_wanted_date()
            self._debug(
                f"No wanted date in request; defaulting to "
                f"{WANTED_DATE_DEFAULT_WORKING_DAYS} working day(s) out: {wanted_date}"
            )
        else:
            self._debug(f"Wanted date from request normalized to: {wanted_date}")

        self.wait_for_spinner_to_disappear()
        if not super().is_visible(self.WANTED_DATE_INPUT):
            self._debug("wantedDate input not found on Estimate Summary; skipping")
            return wanted_date

        field = self.find(self.WANTED_DATE_INPUT)
        field.click()
        # Select-all + delete instead of fill() so the PrimeNG/Angular input
        # registers the change; fill() sets .value without firing the events
        # the calendar binding listens for, so the value gets reverted.
        field.press("Control+a")
        field.press("Delete")
        field.press_sequentially(wanted_date, delay=50)
        # Commit the value and close any datepicker overlay the field may open
        # so it does not obscure the print button.
        field.press("Enter")
        field.blur()
        self.wait_for_spinner_to_disappear()
        actual = field.input_value()
        self._debug(f"wantedDate set to '{wanted_date}'; input now reads '{actual}'")
        return wanted_date

    def _extract_wanted_date(self, quote_record: Dict[str, Any]) -> str:
        """Pull a date string from the request under any of the known keys."""
        if not isinstance(quote_record, dict):
            return ""
        candidates = [quote_record]
        requirements = quote_record.get("requirements")
        if isinstance(requirements, dict):
            candidates.append(requirements)
        elif isinstance(requirements, list):
            candidates.extend(r for r in requirements if isinstance(r, dict))

        keys = (
            "wanted_date",
            "wantedDate",
            "due_date",
            "dueDate",
            "delivery_date",
            "deliveryDate",
            "date",
        )
        for source in candidates:
            for key in keys:
                value = source.get(key)
                if value not in (None, ""):
                    return str(value).strip()
        return ""

    def _normalize_wanted_date(self, raw: str) -> str:
        """Normalize an arbitrary date string to ``M/D/YYYY``.

        Returns an empty string if the value cannot be parsed.
        """
        raw = (raw or "").strip()
        if not raw:
            return ""

        # Drop any time component (e.g. ISO "2026-06-27T00:00:00").
        raw = raw.split("T")[0].split(" ")[0]

        formats = (
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%d/%m/%Y",
            "%m-%d-%Y",
            "%d-%m-%Y",
            "%Y/%m/%d",
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(raw, fmt).date()
                return self._format_wanted_date(parsed)
            except ValueError:
                continue
        self._debug(f"Could not parse wanted date '{raw}'; will use default")
        return ""

    def _default_wanted_date(self) -> str:
        return self._format_wanted_date(
            self._add_working_days(date.today(), WANTED_DATE_DEFAULT_WORKING_DAYS)
        )

    @staticmethod
    def _add_working_days(start: date, working_days: int) -> date:
        """Advance ``start`` by ``working_days`` Mon-Fri days."""
        current = start
        remaining = max(working_days, 0)
        while remaining > 0:
            current += timedelta(days=1)
            if current.weekday() < 5:  # Mon-Fri
                remaining -= 1
        return current

    @staticmethod
    def _format_wanted_date(value: date) -> str:
        """Format as month_number/date/year with no leading zeros."""
        return f"{value.month}/{value.day}/{value.year}"

    def click_add_job(self) -> None:
        self._debug("Opening Add menu on Estimate Summary and selecting Add Job")
        self.switch_to_tab()
        self.wait_for_spinner_to_disappear()
        self.wait_for_visible(self.ADD_BUTTON)
        self.click(self.ADD_BUTTON)
        self.wait_for_visible(self.ADD_JOB_BUTTON)
        self.click(self.ADD_JOB_BUTTON)
        self.wait_for_spinner_to_disappear()

    def _download_headless(self, temp_dir: Path) -> Path:
        download_timeout = max(self._timeout_ms, 120_000)

        with self.page.expect_download(timeout=download_timeout) as download_info:
            self.click(self.US685_E_ESTIMATE_BUTTON)
            self._debug("US685 E-Estimate clicked; waiting for download")

        download = download_info.value
        suggested = download.suggested_filename or f"invoice_{int(time.time())}.pdf"
        filename = self._sanitize_filename(suggested)
        target_path = self._unique_path(temp_dir / filename)

        download.save_as(target_path)
        self._debug(f"Invoice downloaded to: {target_path}")

        failure = download.failure()
        if failure:
            raise RuntimeError(f"Download failed: {failure}")

        return target_path

    def _download_headed(self, temp_dir: Path) -> Path:
        # In headed mode Chromium opens the PDF in a new tab via window.open().
        # Wait for that tab, grab the URL, download it via urllib, then close
        # the tab so the flow returns to the main page and can proceed to logout.
        with self.page.context.expect_page(timeout=max(self._timeout_ms, 120_000)) as new_page_info:
            self.click(self.US685_E_ESTIMATE_BUTTON)
            self._debug("US685 E-Estimate clicked; waiting for generated document tab")

        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=max(self._timeout_ms, 120_000))

        try:
            download_url = self._wait_for_download_url(new_page)
            self._debug(f"Resolved invoice download URL: {download_url}")
            cookies = self.page.context.cookies()
            saved_path = self._download_invoice(download_url, temp_dir, cookies, new_page)
            self._debug(f"Invoice downloaded to: {saved_path}")
            return saved_path
        finally:
            try:
                new_page.wait_for_timeout(5000)
                new_page.close()
                self._debug("Closed generated document tab; returning to main page")
            except Exception:
                pass

    def _create_prospect(self) -> None:
        self._debug("Creating prospect before downloading estimate")
        self.wait_for_spinner_to_disappear()

        if super().is_visible(self.CREATE_PROSPECT_BUTTON):
            self._debug("Create Prospect button found directly; clicking it")
            self.click(self.CREATE_PROSPECT_BUTTON)
            self.wait_for_spinner_to_disappear()
            return

        self._debug("Create Prospect button not found directly; opening three-dots menu")
        self.wait_for_visible(self.THREE_DOTS_BUTTON)
        self.click(self.THREE_DOTS_BUTTON)
        self.wait_for_visible(self.CREATE_PROSPECT_LINK)
        self.click(self.CREATE_PROSPECT_LINK)
        self.wait_for_spinner_to_disappear()

    def _wait_for_download_url(self, new_page) -> str:
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

        raise PlaywrightTimeoutError("Unable to resolve generated invoice download URL")

    def _download_invoice(self, url: str, target_dir: Path, cookies: list, new_page) -> Path:
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
            filename = self._build_filename(url, response.headers.get("Content-Disposition", ""))
            target_path = self._unique_path(target_dir / filename)
            with target_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        return target_path

    def _build_filename(self, url: str, content_disposition: str) -> str:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition or "", re.I)
        if match:
            filename = unquote(match.group(1).strip())
        else:
            filename = Path(urlparse(url).path).name or f"invoice_{int(time.time())}.pdf"

        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or f"invoice_{int(time.time())}.pdf"
        if "." not in filename:
            filename = f"{filename}.pdf"
        return filename

    def _sanitize_filename(self, filename: str) -> str:
        filename = unquote(filename)
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
        if not filename:
            filename = f"invoice_{int(time.time())}"
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
