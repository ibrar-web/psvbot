import logging
import time
from typing import Any, Dict, List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.config import DEBUG
from app.v1.modules.bot.etimate_history.pages.estimate_history_page import EstimateHistoryPage

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
    # The grid renders TWO "acc_info_celldata" links per row — one in the
    # Estimate # column (col_data_invoiceNumber, e.g. "US685-39413") and
    # one in the Invoice # column (col_data_convertedInvoiceNo, e.g.
    # "50059") — confirmed live. An unscoped selector's .first matches the
    # Estimate # cell (it renders first in the row), so this MUST be
    # scoped to the Invoice # column specifically.
    INVOICE_NUMBER_CELL_LINK = (
        "xpath=//td[@id='col_data_convertedInvoiceNo']"
        "//a[contains(@class,'acc_info_celldata')]"
    )

    # Confirmed exact routes: clicking a record on the grid either (a) stays
    # on the grid URL ("#/history/estimatehistory") and shows the "locked by
    # user" dialog, or (b) navigates to INVOICE_PAGE_URL_FRAGMENT. Using
    # Playwright's native page.wait_for_url() — tracked at the driver/CDP
    # level, not evaluated as JS inside the page — so it survives the
    # transient reload that made wait_for_function throw "Target page,
    # context or browser has been closed".
    INVOICE_PAGE_URL_FRAGMENT = "#/invoicing/invoice-page"

    # An estimate's line-items tab is labeled "Estimate Summary"; once
    # converted to an invoice, PrintSmith relabels the SAME tab "Invoice
    # Summary" — confirmed live. Match either.
    SUMMARY_TAB = (
        "xpath=//li[@role='tab' and "
        "(.//span[normalize-space()='Estimate Summary'] "
        "or .//span[normalize-space()='Invoice Summary'])]"
    )
    _SUMMARY_TAB_ACTIVE_JS = """() => {
        const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
        const target = tabs.find(t => {
            const text = (t.innerText || "").trim();
            return text === "Estimate Summary" || text === "Invoice Summary";
        });
        return !!target && target.getAttribute("aria-selected") === "true";
    }"""

    _JOB_DETAILS_TAB_ACTIVE_JS = """() => {
        const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
        const target = tabs.find(t => (t.innerText || "").includes("Job Details"));
        return !!target && target.getAttribute("aria-selected") === "true";
    }"""

    # Multi-Part jobs add a 4th top-level tab ("Job Parts", between Job
    # Details and Estimate Summary) hosting a p-treetable ("jobparts_grid")
    # listing each part — same row shape as the outer Estimate/Invoice
    # Summary tree table (a hidden index span wrapping "job-N"), just under
    # its own name attribute so it doesn't collide with the outer table's.
    JOB_PARTS_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Job Parts']]"
    _JOB_PARTS_TAB_ACTIVE_JS = """() => {
        const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
        const target = tabs.find(t => (t.innerText || "").includes("Job Parts"));
        return !!target && target.getAttribute("aria-selected") === "true";
    }"""
    JOBPARTS_GRID_TBODY = "p-treetable[name='jobparts_grid'] tbody.ui-treetable-tbody"
    MULTIPART_DESCRIPTION_FIELD = "xpath=//textarea[@name='multipart-descriptionField']"
    MULTIPART_NOTES_FIELD = "xpath=//textarea[@name='multipart-jobnotesField']"

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
        # Same race as clear_filters(): the spinner can take a couple of
        # seconds to actually appear after Enter triggers the grid reload,
        # so checking immediately can pass before the filter has actually
        # applied — leaving the previous (unfiltered) row still clickable.
        self.page.wait_for_timeout(3000)
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
        self._enter_estimate_summary()

        initial_row_count = self._row_count()
        self._debug(f"Estimate/Invoice Summary tree table row count: {initial_row_count}. URL: {self.page.url}")
        if initial_row_count == 0:
            logger.warning(
                "Estimate/Invoice Summary tree table has 0 rows right after "
                "entering the tab (URL=%s) — the table may not have finished "
                "rendering yet",
                self.page.url,
            )

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
                try:
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
                except Exception:
                    # One unreadable job row shouldn't wipe out every other
                    # row's already-scraped data — log it and keep going.
                    logger.exception(
                        "Failed to scrape job row %s (%s); continuing with remaining rows",
                        index,
                        label,
                    )
                finally:
                    self._enter_estimate_summary()

            index += 1

        self._debug(f"Scraped {len(job_items)} job item(s), {len(other_charges)} other charge(s)")
        return {"job_items": job_items, "other_charges": other_charges}

    def _enter_estimate_summary(self) -> None:
        """Land on the Estimate/Invoice Summary tree table. SUMMARY_TAB
        matches either label so this works for both an estimate ("Estimate
        Summary") and an already-converted invoice ("Invoice Summary") —
        confirmed live that PrintSmith relabels the same tab on conversion.
        """
        self.wait_for_spinner_to_disappear()
        tab_loc = self._loc(self.SUMMARY_TAB).first
        tab_loc.wait_for(state="visible", timeout=self._timeout_ms)
        tab_loc.click()
        self.page.wait_for_function(self._SUMMARY_TAB_ACTIVE_JS, timeout=self._timeout_ms)
        self.wait_for_spinner_to_disappear()
        self._loc("css=tbody.ui-treetable-tbody").first.wait_for(
            state="visible", timeout=self._timeout_ms
        )

    # ------------------------------------------------------------------
    # Multi-Part: "Job Parts" tab + jobparts_grid scrape
    # ------------------------------------------------------------------

    def _enter_job_parts_tab(self) -> None:
        """Land on the "Job Parts" tab's jobparts_grid list. Also used to
        get BACK to the list after opening one part's fields — opening a
        part happens within this same already-active tab (unlike Job
        Details -> Estimate Summary, there's no separate tab to switch
        back to), so this re-clicks JOB_PARTS_TAB every time.
        """
        self.wait_for_spinner_to_disappear()
        tab_loc = self._loc(self.JOB_PARTS_TAB).first
        tab_loc.wait_for(state="visible", timeout=self._timeout_ms)
        tab_loc.click()
        self.page.wait_for_function(self._JOB_PARTS_TAB_ACTIVE_JS, timeout=self._timeout_ms)
        self.wait_for_spinner_to_disappear()
        self._loc(f"css={self.JOBPARTS_GRID_TBODY}").first.wait_for(
            state="visible", timeout=self._timeout_ms
        )

    def _open_part_row(self, row_index: int) -> None:
        """Open one jobparts_grid row's fields. Unlike _open_job_row, this
        does NOT check for a "Job Details" tab becoming active — opening a
        part stays on the "Job Parts" tab throughout, only its own
        sub-form content changes, so _wait_for_job_details_form_ready()
        (tab-agnostic — just waits for Job Method to show a value) is the
        only readiness signal needed.
        """
        row_loc = self._loc(f"css={self.JOBPARTS_GRID_TBODY} > tr").nth(row_index)
        desc_loc = row_loc.locator(".job_description")

        if desc_loc.count() > 0:
            desc_loc.first.scroll_into_view_if_needed(timeout=self._timeout_ms)
            desc_loc.first.click(timeout=self._timeout_ms)
        else:
            row_loc.scroll_into_view_if_needed(timeout=self._timeout_ms)
            row_loc.click(timeout=self._timeout_ms)

        self.wait_for_spinner_to_disappear()
        self._wait_for_job_details_form_ready()

    def _read_multipart_parts(self) -> List[Dict[str, Any]]:
        """Walk a Multi-Part job's own "Job Parts" tab (jobparts_grid) and
        read every part, reusing _read_job_details/_read_job_charges for
        each one exactly like scrape_invoice does for top-level jobs.
        """
        self._enter_job_parts_tab()

        parts: List[Dict[str, Any]] = []
        index = 0
        while True:
            self.wait_for_spinner_to_disappear()
            if index >= self._row_count(table_selector=self.JOBPARTS_GRID_TBODY):
                break

            classification = self._classify_row(
                index,
                table_selector=self.JOBPARTS_GRID_TBODY,
                index_span_name="multipart_job_charge_index",
            )
            if not classification:
                self._debug(f"Part row {index} did not classify as a job; skipping")
                index += 1
                continue

            label = classification["label"]
            self._debug(f"Part row {index} ({label}): opening")
            try:
                self._open_part_row(index)
                part_method = self._read_job_method()
                part_details = self._read_job_details(part_method, top_level=False)
                part_charges = self._read_job_charges()
                parts.append(
                    {
                        "job_name": label,
                        "job_method": part_method,
                        **part_details,
                        "job_charges": part_charges,
                    }
                )
            except Exception:
                logger.exception(
                    "Failed to scrape multi-part part %s (%s); continuing with remaining parts",
                    index,
                    label,
                )
            finally:
                self._enter_job_parts_tab()

            index += 1

        self._debug(f"Scraped {len(parts)} multi-part part(s)")
        return parts

    def _row_count(self, table_selector: str = "tbody.ui-treetable-tbody") -> int:
        return int(
            self.page.evaluate(
                "(sel) => document.querySelectorAll(sel + ' > tr').length",
                table_selector,
            )
            or 0
        )

    def _classify_row(
        self,
        row_index: int,
        table_selector: str = "tbody.ui-treetable-tbody",
        index_span_name: str = "job_charge_index",
    ) -> Optional[Dict[str, str]]:
        return self.page.evaluate(
            """({tableSelector, rowIndex, indexSpanName}) => {
                const rows = document.querySelectorAll(tableSelector + ' > tr');
                const row = rows[rowIndex];
                if (!row) return null;
                const indexSpan = row.querySelector(`span[name='${indexSpanName}']`);
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
            {"tableSelector": table_selector, "rowIndex": row_index, "indexSpanName": index_span_name},
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
        # A JS-dispatched element.click() (as used here previously) does not
        # reliably trigger Angular's bound click handlers — same lesson
        # learned the hard way with the Record Lock scope dropdown. Use real
        # Playwright locator clicks instead.
        row_loc = self._loc("css=tbody.ui-treetable-tbody > tr").nth(row_index)
        desc_loc = row_loc.locator(".job_description")

        clicked_description = desc_loc.count() > 0
        if clicked_description:
            desc_loc.first.scroll_into_view_if_needed(timeout=self._timeout_ms)
            desc_loc.first.click(timeout=self._timeout_ms)
        else:
            row_loc.scroll_into_view_if_needed(timeout=self._timeout_ms)
            row_loc.click(timeout=self._timeout_ms)

        self.wait_for_spinner_to_disappear()
        if not self._is_job_details_tab_active(timeout_ms=3000):
            self._debug(
                f"Row {row_index}: clicking "
                f"{'.job_description' if clicked_description else 'the row'} "
                "did not open Job Details; retrying on the row itself"
            )
            row_loc.scroll_into_view_if_needed(timeout=self._timeout_ms)
            row_loc.click(timeout=self._timeout_ms)
            self.wait_for_spinner_to_disappear()
            self._is_job_details_tab_active(timeout_ms=self._timeout_ms, raise_on_timeout=True)

        self._wait_for_job_details_form_ready()

    def _wait_for_job_details_form_ready(self) -> None:
        """The tab can report aria-selected="true" before the job-method-
        specific sub-form has actually finished rendering/binding its
        widgets — the Job Method dropdown renders synchronously, but
        combobox-bound fields (Stock, Finish Size) can lag behind it.
        Wait for Job Method to show a non-empty value as the readiness
        signal, then wait for the spinner to settle, then give the slower
        comboboxes a couple more seconds to catch up before anything
        starts reading fields.
        """
        try:
            self.page.wait_for_function(
                """() => {
                    const el = document.querySelector("kendo-dropdownlist[name='jobMethodList'] span.k-input");
                    return !!(el && (el.innerText || el.textContent || '').trim());
                }""",
                timeout=self._timeout_ms,
            )
        except PlaywrightTimeoutError:
            self._debug("Job Method field did not show a value in time; reading fields anyway")
        self.wait_for_spinner_to_disappear()
        self.page.wait_for_timeout(2000)

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

    def _read_kendo_text(self, name: str, *, timeout_ms: int = 8000) -> str:
        """Read a Kendo widget's currently displayed value by its `name`
        attribute. kendo-dropdownlist renders its value in a <span
        class="k-input"> (read via innerText), while kendo-combobox (e.g.
        choose_stock, finishSize, parentSize, runsize) renders it in an
        <input class="k-input"> instead (confirmed against job_details.py's
        own selectors for these same widgets, e.g. add_size's
        "input.k-input" target) — its value lives in .value, not text
        content, so innerText on it is always empty. Try both.

        The widget can be visible before its value finishes async-binding
        — confirmed live: stock/finish size intermittently came back empty
        even though the widget itself was already visible and the job
        method had already loaded. Poll for a non-empty value instead of
        reading once right after visibility.
        """
        locator = self._loc(
            f"xpath=//kendo-dropdownlist[@name='{name}'] | //kendo-combobox[@name='{name}']"
        ).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            logger.warning("_read_kendo_text(%s): widget never became visible", name)
            return ""

        def read_once() -> str:
            input_el = locator.locator("input.k-input")
            if input_el.count() > 0:
                try:
                    value = input_el.first.input_value()
                    if value:
                        return value.strip()
                except Exception:
                    pass

            span_el = locator.locator("span.k-input")
            if span_el.count() > 0:
                return (span_el.first.inner_text() or "").strip()

            return ""

        deadline = time.monotonic() + (timeout_ms / 1000)
        last_value = ""
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            last_value = read_once()
            if last_value:
                self._debug(f"_read_kendo_text({name}): confirmed value '{last_value}' after {attempts} attempt(s)")
                return last_value
            self.page.wait_for_timeout(300)

        logger.warning(
            "_read_kendo_text(%s): still empty after %s attempt(s)/%sms — value was never bound",
            name,
            attempts,
            timeout_ms,
        )
        return last_value

    def _read_print_sides(self) -> str:
        """Read which button ("Simplex"/"Duplex") is currently active in
        the Print button-group next to the "Print" label — confirmed live
        in invoice_job_details.html:594-621: this control has no name
        attribute, so it's located via its label sibling. Returns "" if
        this job method doesn't render a Print toggle.
        """
        return (
            self.page.evaluate(
                """() => {
                    const labels = Array.from(document.querySelectorAll('label'));
                    const printLabel = labels.find(
                        l => (l.innerText || l.textContent || '').trim() === 'Print'
                    );
                    if (!printLabel) return '';
                    const group = printLabel.nextElementSibling;
                    if (!group) return '';
                    const active = group.querySelector('button.k-state-active, button.active');
                    if (!active) return '';
                    return (active.innerText || active.textContent || '').trim();
                }"""
            )
            or ""
        ).strip()

    def _field_value(self, selector: str) -> str:
        locator = self._loc(selector).first
        try:
            locator.wait_for(state="visible", timeout=8000)
        except PlaywrightTimeoutError:
            return ""
        try:
            return (locator.input_value() or "").strip()
        except Exception:
            return ""

    def _read_job_method(self) -> str:
        return self._read_kendo_text("jobMethodList")

    def _read_job_details(self, job_method: str, *, top_level: bool = True) -> Dict[str, Any]:
        method_key = (job_method or "").strip().lower()
        details: Dict[str, Any] = {
            "product": self._read_kendo_text("productsList"),
            "location": self._read_kendo_text("locationList"),
            "job_comment": self._field_value("xpath=//input[@name='job_comment']"),
        }

        if method_key == "multi-part":
            if not top_level:
                logger.warning(
                    "Nested multi-part part encountered; not supported, "
                    "returning no sub-parts"
                )
                details["description"] = ""
                details["notes"] = ""
                details["parts"] = []
            else:
                details["description"] = self._field_value(self.MULTIPART_DESCRIPTION_FIELD)
                details["notes"] = self._field_value(self.MULTIPART_NOTES_FIELD)
                details["parts"] = self._read_multipart_parts()
        elif method_key == "charges only":
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
            details["finish_size"] = self._read_kendo_text("finishSize")
            details["sides"] = self._read_print_sides()
            if not details["description"]:
                logger.warning(
                    "Unrecognized job method '%s'; description field may "
                    "not have been captured for this job",
                    job_method,
                )
            if not details["stock"] or not details["finish_size"]:
                logger.warning(
                    "Job method '%s': stock='%s' finish_size='%s' — one or "
                    "both came back empty after the full poll/retry window",
                    job_method,
                    details["stock"],
                    details["finish_size"],
                )
            else:
                self._debug(
                    f"Confirmed stock='{details['stock']}' "
                    f"finish_size='{details['finish_size']}' for job_method='{job_method}'"
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
