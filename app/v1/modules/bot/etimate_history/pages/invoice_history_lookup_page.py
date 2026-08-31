import logging
from typing import Any, Dict, List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.etimate_history.pages.estimate_history_page import EstimateHistoryPage
from app.v1.modules.bot.pages.invoice_page.estimated_summary import EstimatedSummaryTab

logger = logging.getLogger(__name__)


class InvoiceHistoryLookupPage(EstimateHistoryPage):
    """Estimate History screen, single invoice lookup + Estimate Summary scrape.

    Searches by Invoice # (not Estimate #), opens the matching record, then
    walks the invoice's Estimate Summary tree table row by row: charge rows
    (invoice-wide charges) are read directly from their own cells, while job
    rows carry no usable data in the row itself, so each one is opened in
    Job Details, scraped, and the flow returns to Estimate Summary before
    continuing to the next row.
    """

    INVOICE_NUMBER_FILTER_INPUT = "xpath=//input[@name='filter_convertedInvoiceNo_input']"
    # The "Invoice #" cell link. Same grid component/class as the Estimate #
    # column used by the previous estimate-id lookup — confirm live if
    # PrintSmith renders a different link class for this column.
    INVOICE_NUMBER_CELL_LINK = "xpath=//a[contains(@class,'acc_info_celldata')]"

    # Confirmed exact routes: clicking a record on the grid either (a) stays
    # on the grid URL ("#/history/estimatehistory") and shows the "locked by
    # user" dialog, or (b) navigates to INVOICE_PAGE_URL_FRAGMENT. Using
    # Playwright's native page.wait_for_url() — tracked at the driver/CDP
    # level, not evaluated as JS inside the page — so it survives the
    # transient reload that made wait_for_function throw "Target page,
    # context or browser has been closed".
    INVOICE_PAGE_URL_FRAGMENT = "#/invoicing/invoice-page"

    _JOB_DETAILS_TAB_ACTIVE_JS = """() => {
        const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
        const target = tabs.find(t => (t.innerText || "").includes("Job Details"));
        return !!target && target.getAttribute("aria-selected") === "true";
    }"""

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][InvoiceHistoryLookupPage] {message}")
        logger.info(message)

    def search_by_invoice_id(self, invoice_id: str) -> None:
        self._debug(f"Filtering Estimate History grid by Invoice #: {invoice_id}")
        self.wait_for_spinner_to_disappear()
        filter_loc = self._loc(self.INVOICE_NUMBER_FILTER_INPUT).first
        filter_loc.wait_for(state="visible", timeout=self._timeout_ms)
        filter_loc.click()
        filter_loc.fill(str(invoice_id))
        filter_loc.press("Enter")
        self.wait_for_spinner_to_disappear()

    def open_first_search_result(self, invoice_id: str = "") -> None:
        self._debug("Waiting for filtered Estimate History row")
        self.wait_for_spinner_to_disappear()
        link_loc = self._loc(self.INVOICE_NUMBER_CELL_LINK).first
        link_loc.wait_for(state="visible", timeout=self._timeout_ms)
        link_loc.click()
        self.wait_for_spinner_to_disappear()

        self._open_record_with_lock_retry(invoice_id)
        self.wait_for_spinner_to_disappear()

    def _open_record_with_lock_retry(self, invoice_id: str) -> None:
        # Spinner has already settled. Either we've already navigated to the
        # invoice/detail page, or PrintSmith is showing a "record is locked
        # by user X" alert and we're still on the grid URL.
        if self.INVOICE_PAGE_URL_FRAGMENT in self.page.url:
            return

        if self.dismiss_locked_record_dialog_if_present():
            self._debug(f"Invoice {invoice_id} is locked; releasing all record locks and retrying")
            self.unlock_all_records()
            link_loc = self._loc(self.INVOICE_NUMBER_CELL_LINK).first
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

    def search_and_open_by_invoice_id(self, invoice_id: str) -> None:
        # Clear any filters left over from a previous session/state before
        # applying our own, otherwise the Invoice # match can get filtered
        # out entirely.
        self.clear_filters()
        self.search_by_invoice_id(invoice_id)
        self.open_first_search_result(invoice_id)
        self._debug(f"Opened invoice record for invoice_id={invoice_id}. URL: {self.page.url}")

    # ------------------------------------------------------------------
    # Estimate Summary tree-table scrape
    # ------------------------------------------------------------------

    def scrape_invoice(self) -> Dict[str, Any]:
        """Walk the Estimate Summary tree table row by row and return
        {"job_items": [...], "other_charges": [...]}.

        Row count, order, and job/charge mix are fully dynamic per invoice —
        every row is classified independently by its own job_charge_index
        span, and the row list is re-queried fresh on every iteration since
        opening/leaving Job Details re-renders the tbody.
        """
        summary_tab = EstimatedSummaryTab(self.page, self.timeout)
        summary_tab.switch_to_tab()

        job_items: List[Dict[str, Any]] = []
        other_charges: List[Dict[str, Any]] = []
        index = 0

        while True:
            self.wait_for_spinner_to_disappear()
            if index >= self._row_count():
                break

            classification = self._classify_row(index)
            if not classification:
                self._debug(f"Row {index} did not classify as job or charge; skipping")
                index += 1
                continue

            kind = classification["kind"]
            label = classification["label"]

            if kind == "charge":
                charge = self._read_charge_row(index)
                self._debug(f"Row {index} ({label}) is a charge row: {charge}")
                other_charges.append(charge)
            else:
                self._debug(f"Row {index} ({label}) is a job row; opening Job Details")
                self._open_job_row(index)
                job_method = self._read_job_method()
                details = self._read_job_details(job_method)
                job_charges = self._read_job_charges()
                job_items.append(
                    {
                        "job_name": label,
                        "job_method": job_method,
                        **details,
                        "job_charges": job_charges,
                    }
                )
                summary_tab.switch_to_tab()

            index += 1

        self._debug(f"Scraped {len(job_items)} job item(s), {len(other_charges)} other charge(s)")
        return {"job_items": job_items, "other_charges": other_charges}

    def _row_count(self) -> int:
        return int(
            self.page.evaluate(
                "() => document.querySelectorAll('tbody.ui-treetable-tbody > tr').length"
            )
            or 0
        )

    def _classify_row(self, row_index: int) -> Optional[Dict[str, str]]:
        return self.page.evaluate(
            """(rowIndex) => {
                const rows = document.querySelectorAll('tbody.ui-treetable-tbody > tr');
                const row = rows[rowIndex];
                if (!row) return null;
                const indexSpan = row.querySelector("span[name='job_charge_index']");
                if (!indexSpan) return null;
                const jobSpan = indexSpan.querySelector("span[name^='job-']");
                if (jobSpan) {
                    return { kind: 'job', label: (jobSpan.innerText || jobSpan.textContent || '').trim() };
                }
                const chargeSpan = indexSpan.querySelector("span[name^='charge-']");
                if (chargeSpan) {
                    return { kind: 'charge', label: (chargeSpan.innerText || chargeSpan.textContent || '').trim() };
                }
                return null;
            }""",
            row_index,
        )

    def _read_charge_row(self, row_index: int) -> Dict[str, str]:
        raw = self.page.evaluate(
            """(rowIndex) => {
                const rows = document.querySelectorAll('tbody.ui-treetable-tbody > tr');
                const row = rows[rowIndex];
                if (!row) return null;
                const tds = row.querySelectorAll(':scope > td');
                const descTd = tds[1];
                const priceTd = tds[3];
                const qtyTd = tds[4];
                const descEl = descTd?.querySelector('.charge_description');
                const priceInput = priceTd?.querySelector('input');
                const qtyInput = qtyTd?.querySelector('input');
                return {
                    charge_name: (descEl?.innerText || descEl?.textContent || '').trim(),
                    charge_price: priceInput ? priceInput.value : '',
                    quantity: qtyInput ? qtyInput.value : '',
                };
            }""",
            row_index,
        ) or {}
        return {
            "charge_name": str(raw.get("charge_name") or "").strip(),
            "charge_price": self._strip_currency(raw.get("charge_price")),
            "quantity": str(raw.get("quantity") or "").strip(),
        }

    def _open_job_row(self, row_index: int) -> None:
        self.page.evaluate(
            """(rowIndex) => {
                const rows = document.querySelectorAll('tbody.ui-treetable-tbody > tr');
                const row = rows[rowIndex];
                if (!row) return false;
                const target = row.querySelector('.job_description') || row;
                target.scrollIntoView({ block: 'center' });
                target.click();
                return true;
            }""",
            row_index,
        )
        self.wait_for_spinner_to_disappear()
        if self._is_job_details_tab_active(timeout_ms=3000):
            return

        self._debug(
            f"Row {row_index}: clicking .job_description did not open Job Details; retrying on the row itself"
        )
        self.page.evaluate(
            """(rowIndex) => {
                const rows = document.querySelectorAll('tbody.ui-treetable-tbody > tr');
                const row = rows[rowIndex];
                if (!row) return false;
                row.scrollIntoView({ block: 'center' });
                row.click();
                return true;
            }""",
            row_index,
        )
        self.wait_for_spinner_to_disappear()
        self._is_job_details_tab_active(timeout_ms=self._timeout_ms, raise_on_timeout=True)

    def _is_job_details_tab_active(self, *, timeout_ms: int, raise_on_timeout: bool = False) -> bool:
        try:
            self.page.wait_for_function(self._JOB_DETAILS_TAB_ACTIVE_JS, timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            if raise_on_timeout:
                raise
            return False

    # ------------------------------------------------------------------
    # Job Details reads (no writes — this flow only scrapes)
    # ------------------------------------------------------------------

    def _read_kendo_text(self, name: str) -> str:
        locator = self._loc(
            f"xpath=//kendo-dropdownlist[@name='{name}'] | //kendo-combobox[@name='{name}']"
        ).first
        try:
            locator.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return ""
        return (locator.locator(".k-input").inner_text() or "").strip()

    def _field_value(self, selector: str) -> str:
        locator = self._loc(selector).first
        try:
            locator.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return ""
        try:
            return (locator.input_value() or "").strip()
        except Exception:
            return ""

    def _read_job_method(self) -> str:
        return self._read_kendo_text("jobMethodList")

    def _read_job_details(self, job_method: str) -> Dict[str, str]:
        method_key = (job_method or "").strip().lower()
        details: Dict[str, str] = {
            "product": self._read_kendo_text("productsList"),
            "location": self._read_kendo_text("locationList"),
            "job_comment": self._field_value("xpath=//input[@name='job_comment']"),
        }

        if method_key == "charges only":
            details["description"] = self._field_value(
                "xpath=//textarea[@name='charges-descriptionField']"
            )
            details["notes"] = self._field_value(
                "xpath=//textarea[@name='charges-jobnotesField']"
            )
        elif method_key == "sublet":
            details["description"] = self._field_value(
                "xpath=//textarea[@name='outside-descriptionField']"
            )
            logger.warning(
                "Sublet job method scraping is best-effort and not fully "
                "verified against a live invoice: %s",
                job_method,
            )
        else:
            details["description"] = self._field_value(
                "xpath=//textarea[@name='digital-descriptionField']"
            )
            details["stock"] = self._read_kendo_text("choose_stock")
            details["stock_color"] = self._read_kendo_text("stockColorList")
            if not details["description"]:
                logger.warning(
                    "Unrecognized job method '%s'; description field may "
                    "not have been captured for this job",
                    job_method,
                )

        details["quantity"] = self._field_value("xpath=//input[@name='qty-label-ctext']")
        details["unit_per_side"] = self._field_value(
            "xpath=//input[@name='unitperside-label-ctext']"
        )
        details["price"] = self._field_value("xpath=//input[@name='price-label-text']")
        return details

    def _read_job_charges(self) -> List[Dict[str, str]]:
        raw_rows = self.page.evaluate(
            """() => {
                const rows = Array.from(document.querySelectorAll("div[id^='charge_index_']"));
                const results = [];
                for (const row of rows) {
                    const hasPressBadge = !!row.querySelector('.charge-desc .badge-container .tag_press');
                    if (hasPressBadge) continue;
                    const nameEl = row.querySelector('.charge_desc_label');
                    const cols = row.querySelectorAll(':scope > div');
                    const qtyEl = cols[1] ? cols[1].querySelector('span') : null;
                    const priceEl = cols[2] ? cols[2].querySelector('span') : null;
                    results.push({
                        charge_name: (nameEl?.innerText || nameEl?.textContent || '').trim(),
                        quantity: (qtyEl?.innerText || qtyEl?.textContent || '').trim(),
                        charge_price: (priceEl?.innerText || priceEl?.textContent || '').trim(),
                    });
                }
                return results;
            }"""
        ) or []
        return [
            {
                "charge_name": str(row.get("charge_name") or "").strip(),
                "quantity": str(row.get("quantity") or "").strip(),
                "charge_price": self._strip_currency(row.get("charge_price")),
            }
            for row in raw_rows
        ]

    @staticmethod
    def _strip_currency(value: Any) -> str:
        return str(value or "").replace("$", "").replace(",", "").strip()
