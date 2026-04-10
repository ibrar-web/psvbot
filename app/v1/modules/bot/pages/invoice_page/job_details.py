import logging
from collections.abc import Mapping

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class InvalidStockSearchError(Exception):
    pass


class JobDetailsTab(BasePage):
    JOB_DETAILS_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Job Details']]"
    STOCK_PICKER_BUTTON = "xpath=//a[@ptooltip='Stock Picker']"
    STOCK_CONFIRM_BUTTON = "xpath=//button[@name='save_stock_details']"
    STOCK_CANCEL_BUTTON = "xpath=//button[@name='cancel_stock_details']"
    CHARGES_MODAL = "xpath=//div[@id='charges_popup']"
    CHARGES_SEARCH_INPUT = (
        "xpath=//div[@id='charges_popup']"
        "//kendo-combobox[@name='search_combo']//input[contains(@class,'k-input')]"
    )
    ADD_CHARGES_BUTTON = (
        "xpath=//div[@id='charges_popup']//input[@name='save_' and @value='Add Charge']"
    )
    CHARGES_SAVE_BUTTON = "xpath=//input[@name='save_charges' and @value='Done']"
    ADD_JOB_CHARGE_BUTTON = "xpath=//a[@name='add_job_charge_btn']"
    QTY_INPUT = "#qty-label-ctext"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][JobDetailsTab] {message}")
        logger.info(message)

    def wait_until_active(self) -> None:
        self._debug("Waiting for Job Details tab to become active")
        self.wait_for_visible(self.JOB_DETAILS_TAB)
        self.page.wait_for_function(
            """() => {
                const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                const target = tabs.find(t => (t.innerText || "").includes("Job Details"));
                return !!target && target.getAttribute("aria-selected") === "true";
            }""",
            timeout=self._timeout_ms,
        )
        self.page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        self.wait_for_visible(self.STOCK_PICKER_BUTTON)

    def select_stock_from_picker(self, data: Mapping[str, str]) -> None:
        stock_search_term = (data.get("stock_search_term") or "gpa").strip()
        if not stock_search_term:
            self._debug("No stock search term provided; skipping stock picker")
            return

        self._open_stock_picker()
        self._search_stock(stock_search_term)
        self._select_matching_stock_row(stock_search_term)
        self._confirm_stock_selection()

    def configure_price_breakup(self, data: Mapping[str, str]) -> None:
        quantity = str(data.get("price_breakup_quantity") or "").strip()
        charges = data.get("job_charges") or []

        if quantity:
            self._debug(f"Setting Price Breakup quantity: {quantity}")
            qty_loc = self._loc(self.QTY_INPUT).first
            qty_loc.wait_for(state="visible", timeout=self._timeout_ms)
            qty_loc.select_text()
            qty_loc.fill(quantity)
            qty_loc.press("Enter")
            self.wait_for_spinner_to_disappear()

        self._open_add_new_charges_modal()
        self._add_job_charges(charges)

    # ------------------------------------------------------------------
    # Stock picker internals
    # ------------------------------------------------------------------

    def _open_stock_picker(self) -> None:
        self._debug("Opening Stock Picker modal")
        self.wait_for_spinner_to_disappear()
        self.click(self.STOCK_PICKER_BUTTON)
        self._wait_for_stock_confirm_button_visible()
        self._wait_for_stock_name_filter_input()

    def _search_stock(self, term: str) -> None:
        self._debug(f"Starting search for stock term: '{term}'")
        self.wait_for_spinner_to_disappear()
        filter_input = self._wait_for_stock_name_filter_input()
        filter_input.fill("")
        filter_input.fill(term)
        filter_input.press("Enter")
        self._debug("Search text entered; waiting for filtered stock rows")
        self.wait_for_spinner_to_disappear()
        self.page.wait_for_function(
            """(term) => {
                const btn = Array.from(document.querySelectorAll("button[name='save_stock_details']"))
                  .find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                if (!btn) return false;

                const modalRoot = btn.closest(".modal-content") || btn.closest(".modal") || document;
                const rows = Array.from(modalRoot.querySelectorAll("tbody[kendogridtablebody] tr"))
                  .filter(row => {
                    const style = window.getComputedStyle(row);
                    return style.display !== "none" && style.visibility !== "hidden" && row.offsetParent !== null;
                  });

                const stockNames = rows
                  .map(row => {
                    const cell = row.querySelector("td[aria-colindex='1']");
                    return (cell?.innerText || cell?.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  })
                  .filter(Boolean);

                const noDataNode = Array.from(modalRoot.querySelectorAll(
                  ".k-grid-norecords, .k-no-data, .k-grid-nodata, .k-nodata"
                )).find(node => {
                  const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                  return !text || text.includes("no data") || text.includes("no records");
                });

                if (noDataNode) return true;
                if (!stockNames.length) return false;
                if (!term) return true;
                return stockNames.some(text => text.includes(term));
            }""",
            arg=term,
            timeout=self._timeout_ms,
        )

    def _select_matching_stock_row(self, term: str) -> None:
        self._debug(f"Selecting best matching stock row for: {term}")
        self.wait_for_spinner_to_disappear()
        try:
            selected_text = self.page.wait_for_function(
                """(term) => {
                    const btn = Array.from(document.querySelectorAll("button[name='save_stock_details']"))
                      .find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                    if (!btn) return false;

                    const modalRoot = btn.closest(".modal-content") || btn.closest(".modal") || document;
                    const noDataNode = Array.from(modalRoot.querySelectorAll(
                      ".k-grid-norecords, .k-no-data, .k-grid-nodata, .k-nodata"
                    )).find(node => {
                      const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                      return !text || text.includes("no data") || text.includes("no records");
                    });
                    if (noDataNode) return "__NO_MATCH__";

                    const rows = Array.from(modalRoot.querySelectorAll("tbody[kendogridtablebody] tr"))
                      .filter(row => {
                        const style = window.getComputedStyle(row);
                        return style.display !== "none" && style.visibility !== "hidden" && row.offsetParent !== null;
                      });
                    if (!rows.length) return false;

                    const entries = rows.map(row => ({
                      node: row,
                      cell: row.querySelector("td[aria-colindex='1']"),
                      text: (row.querySelector("td[aria-colindex='1']")?.innerText
                        || row.querySelector("td[aria-colindex='1']")?.textContent || "")
                        .replace(/\\s+/g, " ").trim()
                    })).filter(entry => entry.text);

                    let target = entries.find(entry => entry.text.toLowerCase() === term) || null;
                    if (!target) target = entries.find(entry => entry.text.toLowerCase().startsWith(term)) || null;
                    if (!target) target = entries.find(entry => entry.text.toLowerCase().includes(term)) || null;
                    if (!target) return "__NO_MATCH__";

                    const clickable = target.cell || target.node;
                    clickable.scrollIntoView({ block: "center" });
                    clickable.click();
                    return target.text;
                }""",
                arg=term,
                timeout=self._timeout_ms,
            ).json_value()
        except PlaywrightTimeoutError:
            selected_text = "__NO_MATCH__"

        if not selected_text or selected_text == "__NO_MATCH__":
            self._cancel_stock_selection()
            raise InvalidStockSearchError(
                f"Invalid stock search term '{term}': no matching stock found in Stock Picker"
            )
        self._wait_for_stock_row_selected(selected_text)

    def _wait_for_stock_row_selected(self, selected_text: str) -> None:
        normalized_target = " ".join((selected_text or "").split()).strip().lower()
        self.page.wait_for_function(
            """(targetText) => {
                const btn = Array.from(document.querySelectorAll("button[name='save_stock_details']"))
                  .find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                if (!btn) return false;

                const modalRoot = btn.closest(".modal-content") || btn.closest(".modal") || document;
                const rows = Array.from(modalRoot.querySelectorAll("tbody[kendogridtablebody] tr"))
                  .filter(row => {
                    const style = window.getComputedStyle(row);
                    return style.display !== "none" && style.visibility !== "hidden" && row.offsetParent !== null;
                  });

                const selectedRow = rows.find(row => {
                  const cell = row.querySelector("td[aria-colindex='1']");
                  const text = (cell?.innerText || cell?.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  const isSelected =
                    row.getAttribute("aria-selected") === "true" ||
                    row.classList.contains("k-selected") ||
                    row.classList.contains("k-state-selected") ||
                    row.classList.contains("highlightedRow") ||
                    row.querySelector("[aria-selected='true'], .k-selected, .k-state-selected");
                  return text === targetText && isSelected;
                });

                if (selectedRow) return true;
                return !btn.disabled && btn.getAttribute("disabled") === null;
            }""",
            arg=normalized_target,
            timeout=self._timeout_ms,
        )

    def _confirm_stock_selection(self) -> None:
        self._debug("Confirming stock selection")
        self.wait_for_spinner_to_disappear()
        confirm_loc = self._loc(self.STOCK_CONFIRM_BUTTON).first
        confirm_loc.wait_for(state="visible", timeout=self._timeout_ms)
        confirm_loc.click(timeout=self._timeout_ms)
        self.wait_for_spinner_to_disappear()

    def _cancel_stock_selection(self) -> None:
        self._debug("Cancelling stock picker because no matching stock was found")
        self.wait_for_spinner_to_disappear()
        cancel_loc = self._loc(self.STOCK_CANCEL_BUTTON).first
        cancel_loc.wait_for(state="visible", timeout=self._timeout_ms)
        cancel_loc.click(timeout=self._timeout_ms)
        self.wait_for_spinner_to_disappear()

    # ------------------------------------------------------------------
    # Charges modal internals
    # ------------------------------------------------------------------

    def _open_add_new_charges_modal(self) -> None:
        self._debug("Opening Add New Charges modal from Price Breakup")
        self.wait_for_spinner_to_disappear()
        visible_modals_before = self.page.evaluate(
            """() => Array.from(document.querySelectorAll("div.modal")).filter(m => {
                const style = window.getComputedStyle(m);
                return style.display !== "none" && style.visibility !== "hidden";
            }).length"""
        )
        self.click(self.ADD_JOB_CHARGE_BUTTON)
        self.page.wait_for_function(
            """(before) => Array.from(document.querySelectorAll("div.modal")).filter(m => {
                const style = window.getComputedStyle(m);
                return style.display !== "none" && style.visibility !== "hidden";
            }).length > before""",
            arg=visible_modals_before,
            timeout=self._timeout_ms,
        )
        self.wait_for_visible(self.CHARGES_MODAL)
        self.wait_for_spinner_to_disappear()
        self._loc(self.CHARGES_SEARCH_INPUT).first.wait_for(
            state="visible", timeout=self._timeout_ms
        )

    def _add_job_charges(self, charges: list) -> None:
        if not charges:
            self._debug("No job charges provided; closing charges modal")
            self.wait_for_spinner_to_disappear()
            self.click(self.CHARGES_SAVE_BUTTON)
            self.wait_for_spinner_to_disappear()
            return

        for charge in charges:
            term = (charge or "").strip()
            if not term:
                continue
            self._debug(f"Adding charge from search: {term}")
            self._select_charge_from_search(term)

        self._debug("Saving selected job charges")
        self.wait_for_spinner_to_disappear()
        self.click(self.CHARGES_SAVE_BUTTON)
        self.wait_for_spinner_to_disappear()

    def _select_charge_from_search(self, term: str) -> None:
        self._debug(f"Charge search: term={term}")
        self.wait_for_spinner_to_disappear()
        search_input = self._get_ready_charges_search_input()
        self._debug("Charge search: input focused")
        search_input.fill("")
        search_input.fill(term)
        self._debug("Charge search: sending input term")

        self.page.wait_for_function(
            """(term) => {
                const items = Array.from(document.querySelectorAll(
                  ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                ));
                return items
                  .filter(item => {
                    const style = window.getComputedStyle(item);
                    return style.display !== "none" && style.visibility !== "hidden" && item.offsetParent !== null;
                  })
                  .map(item => (item.innerText || item.textContent || "").replace(/\\s+/g, " ").trim())
                  .filter(Boolean).length > 0;
            }""",
            arg=term,
            timeout=self._timeout_ms,
        )

        selected = self.page.wait_for_function(
            """(term) => {
                if (!term) return false;
                const items = Array.from(document.querySelectorAll(
                  ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                ));
                const visibleItems = items.filter(item => {
                  const style = window.getComputedStyle(item);
                  return style.display !== "none" && style.visibility !== "hidden" && item.offsetParent !== null;
                });
                const normalized = visibleItems.map(item => ({
                  node: item,
                  text: (item.innerText || item.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase()
                }));

                const termLower = term.toLowerCase();
                let target = normalized.find(entry => entry.text === termLower)?.node || null;
                if (!target) target = normalized.find(entry => entry.text.startsWith(termLower))?.node || null;
                if (!target) target = normalized.find(entry => entry.text.includes(termLower))?.node || null;

                if (!target) return false;
                target.scrollIntoView({ block: "center" });
                target.click();
                return true;
            }""",
            arg=term,
            timeout=self._timeout_ms,
        ).json_value()
        self._debug("Charge search: selection function ran")
        if not selected:
            raise PlaywrightTimeoutError(f"Could not select charge from search: {term}")

        self.wait_for_spinner_to_disappear()
        self._debug("Charge search: about to confirm selected charge item")
        self._confirm_charge_item(term)

    def _confirm_charge_item(self, term: str) -> None:
        self._debug(f"Confirming selected charge item: {term}")
        confirm_loc = self._loc(self.ADD_CHARGES_BUTTON).first
        confirm_loc.wait_for(state="visible", timeout=self._timeout_ms)
        confirm_loc.click(timeout=self._timeout_ms)
        self.wait_for_spinner_to_disappear()

    def _get_ready_charges_search_input(self):
        self.wait_for_visible(self.CHARGES_MODAL)
        self.wait_for_spinner_to_disappear()
        search_loc = self._loc(self.CHARGES_SEARCH_INPUT).first
        search_loc.wait_for(state="visible", timeout=self._timeout_ms)
        search_loc.click()
        return search_loc

    def _wait_for_stock_confirm_button_visible(self) -> None:
        self.page.wait_for_function(
            """() => {
                const btn = Array.from(document.querySelectorAll("button[name='save_stock_details']"))
                  .find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                return !!btn;
            }""",
            timeout=self._timeout_ms,
        )

    def _wait_for_stock_name_filter_input(self):
        locator = self.page.wait_for_function(
            """() => {
                const btn = Array.from(document.querySelectorAll("button[name='save_stock_details']"))
                  .find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                if (!btn) return null;
                const modalRoot = btn.closest(".modal-content") || btn.closest(".modal") || document;

                const headers = Array.from(modalRoot.querySelectorAll("th[role='columnheader']"));
                const stockHeader = headers.find(th =>
                  (th.innerText || th.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase().includes("stock name")
                );

                if (stockHeader) {
                  const colIndex = stockHeader.getAttribute("aria-colindex");
                  if (colIndex) {
                    const byAria = modalRoot.querySelector(
                      `tr.k-filter-row td[aria-colindex="${colIndex}"] input[kendofilterinput]`
                    );
                    if (byAria) return byAria;
                  }
                  const headerRow = stockHeader.closest("tr");
                  const headerCells = headerRow ? Array.from(headerRow.children) : [];
                  const idx = headerCells.indexOf(stockHeader);
                  if (idx >= 0) {
                    const filterRow = modalRoot.querySelector("tr.k-filter-row");
                    const filterCells = filterRow ? Array.from(filterRow.children) : [];
                    const byIdx = filterCells[idx]?.querySelector("input[kendofilterinput]");
                    if (byIdx) return byIdx;
                  }
                }
                return modalRoot.querySelector("tr.k-filter-row input[kendofilterinput]");
            }""",
            timeout=self._timeout_ms,
        )
        # Return a Playwright ElementHandle-backed locator
        element = locator.as_element()
        if element is None:
            raise PlaywrightTimeoutError("Could not find Stock Name filter input in stock modal")
        return self.page.locator("tr.k-filter-row input[kendofilterinput]").first
