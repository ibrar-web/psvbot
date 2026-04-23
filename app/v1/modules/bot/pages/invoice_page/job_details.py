import logging
from collections.abc import Mapping
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class InvalidStockSearchError(Exception):
    pass


class JobDetailsTab(BasePage):
    CHARGE_SEARCH_TIMEOUT_MS = 3000
    JOB_DETAILS_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Job Details']]"
    JOB_DESCRIPTION_INPUT = "xpath=//textarea[@name='digital-descriptionField']"
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
        stock_search_term = " ".join((data.get("stock_search_term") or "gpa").split())
        if not stock_search_term:
            self._debug("No stock search term provided; skipping stock picker")
            return

        self._open_stock_picker()
        self._search_stock(stock_search_term)
        self._select_matching_stock_row(stock_search_term)
        self._confirm_stock_selection()

    def fill_job_description(self, data: Mapping[str, str]) -> None:
        description = str(data.get("description") or "").strip()
        if not description:
            self._debug("No job description provided; skipping description field")
            return

        self._debug("Filling job description before opening Stock Picker")
        self.wait_for_spinner_to_disappear()
        description_loc = self._loc(self.JOB_DESCRIPTION_INPUT).first
        description_loc.wait_for(state="visible", timeout=self._timeout_ms)
        description_loc.fill(description)
        self.page.evaluate(
            """(value) => {
                const field = document.querySelector("textarea[name='digital-descriptionField']");
                if (!field) return false;
                field.value = value;
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.dispatchEvent(new Event("change", { bubbles: true }));
                return true;
            }""",
            description,
        )
        self.wait_for_spinner_to_disappear()

    def configure_price_breakup(self, data: Mapping[str, str]) -> None:
        quantity = str(data.get("price_breakup_quantity") or "").strip()
        job_charges = data.get("job_charges") or []

        if quantity:
            self._debug(f"Setting Price Breakup quantity: {quantity}")
            qty_loc = self._loc(self.QTY_INPUT).first
            qty_loc.wait_for(state="visible", timeout=self._timeout_ms)
            qty_loc.select_text()
            qty_loc.fill(quantity)
            qty_loc.press("Enter")
            self.wait_for_spinner_to_disappear()

        if not job_charges:
            self._debug("No charges provided; skipping charges modal")
            return

        self._open_add_new_charges_modal()
        self._add_job_charges(job_charges)
        self._debug("Saving selected charges")
        self.wait_for_spinner_to_disappear()
        self.click(self.CHARGES_SAVE_BUTTON)
        self.wait_for_spinner_to_disappear()

    def select_bleed(self) -> None:
        self._debug("Selecting bleed option via paper-calculator icon")
        self.wait_for_spinner_to_disappear()
        self.page.locator("span.dot-paper-calculator-icon").first.click()
        self.wait_for_spinner_to_disappear()

        self._debug("Activating Bleed slider")
        self.page.wait_for_function(
            """() => {
                const labels = Array.from(document.querySelectorAll("label, span, div"))
                    .filter(el => (el.innerText || el.textContent || "").trim().toLowerCase() === "bleed");
                for (const label of labels) {
                    const container = label.closest("div, li, tr, .dot-form__row") || label.parentElement;
                    if (!container) continue;
                    const slider = container.querySelector(".dot-switch-slider");
                    if (slider) return true;
                }
                return false;
            }""",
            timeout=self._timeout_ms,
        )
        activated = self.page.evaluate(
            """() => {
                const labels = Array.from(document.querySelectorAll("label, span, div"))
                    .filter(el => (el.innerText || el.textContent || "").trim().toLowerCase() === "bleed");
                for (const label of labels) {
                    const container = label.closest("div, li, tr, .dot-form__row") || label.parentElement;
                    if (!container) continue;
                    const slider = container.querySelector(".dot-switch-slider");
                    if (!slider) continue;
                    const toggle = container.querySelector("input[type='checkbox']") || slider.previousElementSibling;
                    if (toggle && toggle.tagName === "INPUT") {
                        if (!toggle.checked) toggle.click();
                    } else {
                        slider.click();
                    }
                    return true;
                }
                return false;
            }"""
        )
        if not activated:
            self._debug("Bleed slider not found; skipping")

        self._debug("Clicking Confirm after enabling bleed")
        self.wait_for_spinner_to_disappear()
        confirm_loc = self.page.locator("span", has_text="Confirm").first
        confirm_loc.wait_for(state="visible", timeout=self._timeout_ms)
        confirm_loc.click()
        self.wait_for_spinner_to_disappear()

    def select_sides(self, sides: str) -> None:
        raw_sides = "" if sides is None else str(sides)
        self._debug(f"Sides: user provided value='{raw_sides}'")
        sides = raw_sides.strip().lower()
        if sides not in ("simplex", "duplex"):
            self._debug(f"Sides: invalid or empty normalized value='{sides}'; skipping")
            return

        self._debug(f"Sides: selecting normalized value='{sides}'")
        self.wait_for_spinner_to_disappear()

        if sides == "simplex":
            btn = self.page.locator("button[kendobutton] span", has_text="Simplex").first
        else:
            btn = self.page.locator("button[kendobutton] span", has_text="Duplex").first

        btn.wait_for(state="visible", timeout=self._timeout_ms)
        self._debug(f"Sides: button visible for value='{sides}', clicking")
        btn.click()
        self.wait_for_spinner_to_disappear()
        self._debug(f"Sides: completed for value='{sides}'")

    def add_size(self, size: str) -> None:
        size = (size or "").strip()
        if not size:
            self._debug("No size provided; skipping Finish Size field set default value of PSV")
            return

        self._debug(f"Setting Finish Size: {size}")
        self.wait_for_spinner_to_disappear()

        size_input = self.page.locator(
            "label.dot-form__label:has-text('Finish Size') ~ * input.k-input, "
            "label.dot-form__label:has-text('Finish Size') + * input.k-input"
        ).first
        size_input.wait_for(state="visible", timeout=self._timeout_ms)
        size_input.click()
        size_input.fill("")
        size_input.fill(size)
        size_input.press("Enter")
        self.wait_for_spinner_to_disappear()

    def add_notes(self, notes: str) -> None:
        notes = (notes or "").strip()
        if not notes:
            self._debug("No notes provided; skipping description field")
            return

        self._debug(f"Adding notes to Description field: {notes}")
        appended = self.page.evaluate(
            """(notes) => {
                // Find the label with text "Description"
                const label = Array.from(document.querySelectorAll("label.dot-form__label"))
                    .find(el => (el.innerText || el.textContent || "").trim() === "Description");
                if (!label) return false;

                // Look for textarea in the sibling or parent row
                const row = label.closest(".dot-form__row, .row, .form-group, div") || label.parentElement;
                const field = row
                    ? (row.querySelector("textarea") || row.querySelector("input[type='text']"))
                    : null;
                if (!field) return false;

                // Append with blank line + "Notes:" heading + notes text
                const existing = (field.value || "").trimEnd();
                const separator = existing ? "\\n\\n" : "";
                field.value = existing + separator + "Notes:\\n" + notes;

                // Trigger Angular/React change detection
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.dispatchEvent(new Event("change", { bubbles: true }));
                return true;
            }""",
            notes,
        )

        if not appended:
            self._debug("Description field not found; notes were not added")
        else:
            self.wait_for_spinner_to_disappear()

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
        try:
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
                    const termNorm = term.replace(/\\s+/g, " ").trim().toLowerCase();
                    return stockNames.some(text => text.includes(termNorm));
                }""",
                arg=term,
                timeout=self._timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            logger.error("Timed out waiting for stock rows for '%s' — closing modal", term)
            self._cancel_stock_selection()
            raise Exception("The Entered stock doesn't match any row")


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

                    const termNorm = term.replace(/\\s+/g, " ").trim().toLowerCase();
                    let target = entries.find(entry => entry.text.replace(/\\s+/g, " ").trim().toLowerCase() === termNorm) || null;
                    if (!target) target = entries.find(entry => entry.text.replace(/\\s+/g, " ").trim().toLowerCase().startsWith(termNorm)) || null;
                    if (!target) target = entries.find(entry => entry.text.replace(/\\s+/g, " ").trim().toLowerCase().includes(termNorm)) || null;
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
        self.wait_for_spinner_to_disappear()
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
            self._debug("No job charges provided")
            return

        for charge in charges:
            charge_data = self._normalize_charge(charge)
            term = charge_data["charge_name"]
            if not term:
                continue
            self._debug(f"Adding charge from search: {term}")
            selected = self._select_charge_from_search(term)
            if not selected:
                self._debug(f"Skipping unmatched or empty charge search result: {term}")
                continue
            quantity = charge_data.get("quantity")
            if str(quantity or "").strip():
                quantity_filled = self._fill_charge_input(
                    "input[name='preset_quantity']",
                    quantity,
                )
                if not quantity_filled:
                    self._debug(
                        f"Charge '{term}' does not expose a quantity field; ignoring provided quantity"
                    )
            else:
                self._debug(
                    f"No quantity provided for charge '{term}'; skipping quantity entry"
                )
            self._confirm_charge_item(term)

    def _select_charge_from_search(self, term: str) -> bool:
        self._debug(f"Charge search: term={term}")
        self.wait_for_spinner_to_disappear()
        search_input = self._get_ready_charges_search_input()
        self._debug("Charge search: input focused")
        search_input.fill("")
        search_input.fill(term)
        self._debug("Charge search: sending input term")

        try:
            target_index = self.page.wait_for_function(
                """(term) => {
                    const normalize = str => (str || "")
                        .normalize("NFKD")
                        .replace(/[\\u0300-\\u036f]/g, "")
                        .replace(/\\s+/g, " ")
                        .trim()
                        .toLowerCase();
                    const normalizedTerm = normalize(term);
                    if (!normalizedTerm) return -2;

                    const noDataNode = Array.from(document.querySelectorAll(
                      ".k-animation-container .k-nodata, .k-list .k-nodata, .k-no-data, .k-list-nodata"
                    )).find(node => {
                      const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                      return !text || text.includes("no data") || text.includes("no records");
                    });
                    if (noDataNode) return -2;

                    const allItems = Array.from(document.querySelectorAll(
                      ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                    ));
                    const visibleItems = allItems.filter(item => {
                        const style = window.getComputedStyle(item);
                        return style.display !== "none" && style.visibility !== "hidden" && item.offsetParent !== null;
                    });

                    if (!visibleItems.length) return false;

                    const entries = visibleItems.map((node, idx) => ({
                      idx,
                      text: normalize(node.innerText || node.textContent || "")
                    }));

                    const match = entries.find(e => e.text === normalizedTerm)
                               ?? entries.find(e => e.text.startsWith(normalizedTerm))
                               ?? entries.find(e => e.text.includes(normalizedTerm))
                               ?? null;

                    if (!match) return -2;
                    visibleItems[match.idx].scrollIntoView({ block: "nearest" });
                    return match.idx;
                }""",
                arg=term,
                timeout=self._timeout_ms,
            ).json_value()
        except PlaywrightTimeoutError:
            target_index = -2

        self._debug(f"Charge search: target_index={target_index}")
        if target_index == -2:
            self._debug(f"Charge search found no matching item; skipping charge: {term}")
            return False

        item_locator = self.page.locator(
            ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
        ).filter(has=self.page.locator("visible=true")).nth(target_index)
        item_locator.click(timeout=self._timeout_ms)

        self.wait_for_spinner_to_disappear()
        return True

    def _fill_charge_fields(
        self,
        *,
        quantity: Any = None,
        price: Any = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.wait_for_spinner_to_disappear()
        self._fill_charge_input("input[name='preset_quantity']", quantity)
        self._fill_charge_input("input[name='price']", price)
        self._fill_charge_input("input[name='chargeDesc']", description)
        self._fill_charge_input("input[name='chargeNotes']", notes)

    def _visible_charges_modal(self):
        modal = self.page.locator("div#charges_popup:visible").last
        modal.wait_for(state="visible", timeout=self._timeout_ms)
        return modal

    def _fill_charge_input(self, selector: str, value: Any) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        if not text:
            return False

        modal = self._visible_charges_modal()
        locator = modal.locator(f"{selector}:visible").first
        if locator.count() == 0:
            return False
        locator.wait_for(state="visible", timeout=self._timeout_ms)
        locator.click()
        locator.fill("")
        locator.fill(text)
        locator.press("Tab")
        locator.evaluate(
            """(el, newValue) => {
                el.value = newValue;
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
                el.dispatchEvent(new Event("blur", { bubbles: true }));
            }""",
            text,
        )
        self.wait_for_spinner_to_disappear()
        return True

    def _normalize_charge(self, charge: Any) -> dict[str, Any]:
        if isinstance(charge, Mapping):
            return {
                "charge_name": str(
                    charge.get("charge_name")
                    or charge.get("name")
                    or charge.get("charge")
                    or ""
                ).strip(),
                "quantity": charge.get("quantity"),
                "price": charge.get("price"),
                "description": str(charge.get("description") or "").strip(),
            }
        return {
            "charge_name": str(charge or "").strip(),
            "quantity": None,
            "price": None,
            "description": "",
        }


    def _confirm_charge_item(self, term: str) -> None:
        self._debug(f"Confirming selected charge item: {term}")
        self.wait_for_spinner_to_disappear()
        modal = self._visible_charges_modal()
        confirm_loc = modal.locator("input[name='save_'][value='Add Charge']:visible").last
        confirm_loc.wait_for(state="visible", timeout=self._timeout_ms)
        self.page.wait_for_function(
            """() => {
                const modals = Array.from(document.querySelectorAll("div#charges_popup")).filter(node => {
                    const style = window.getComputedStyle(node);
                    return style.display !== "none" && style.visibility !== "hidden" && node.offsetParent !== null;
                });
                if (!modals.length) return false;
                const modal = modals[modals.length - 1];
                const buttons = Array.from(
                    modal.querySelectorAll("input[name='save_'][value='Add Charge']")
                ).filter(node => {
                    const style = window.getComputedStyle(node);
                    return style.display !== "none" && style.visibility !== "hidden";
                });
                if (!buttons.length) return false;
                return !buttons[buttons.length - 1].disabled;
            }""",
            timeout=self._timeout_ms,
        )
        confirm_loc.click(timeout=self._timeout_ms)
        self.page.wait_for_timeout(500)
        self.wait_for_spinner_to_disappear()

    def _get_ready_charges_search_input(self):
        modal = self._visible_charges_modal()
        self.wait_for_spinner_to_disappear()
        search_loc = modal.locator(
            "kendo-combobox[name='search_combo'] input.k-input:visible"
        ).first
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
