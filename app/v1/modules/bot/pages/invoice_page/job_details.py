import logging
from collections.abc import Mapping

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class JobDetailsTab(BasePage):
    JOB_DETAILS_TAB = "//li[@role='tab' and .//span[normalize-space()='Job Details']]"
    STOCK_PICKER_BUTTON = "//a[@ptooltip='Stock Picker']"
    STOCK_CONFIRM_BUTTON = "//button[@name='save_stock_details']"
    STOCK_CANCEL_BUTTON = "//button[@name='cancel_stock_details']"
    CHARGES_MODAL = "//div[@id='charges_popup']"
    CHARGES_SEARCH_INPUT = (
        CHARGES_MODAL
        + "//kendo-combobox[@name='search_combo']//input[contains(@class,'k-input')]"
    )
    ADD_CHARGES_BUTTON = (
        CHARGES_MODAL + "//input[@name='save_' and @value='Add Charge']"
    )
    CHARGES_SAVE_BUTTON = "//input[@name='save_charges' and @value='Done']"

    ADD_JOB_CHARGE_BUTTON = "//a[@name='add_job_charge_btn']"

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][JobDetailsTab] {message}")
            logger.info(message)

    def wait_until_active(self) -> None:
        self._debug("Waiting for Job Details tab to become active")
        self.wait_for_visible(By.XPATH, self.JOB_DETAILS_TAB)
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                    const target = tabs.find(t => (t.innerText || "").includes("Job Details"));
                    return !!target && target.getAttribute("aria-selected") === "true";
                    """
                )
            )
        )
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        self.wait_for_visible(By.XPATH, self.STOCK_PICKER_BUTTON)

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
            element = self.wait_for_visible(By.ID, "qty-label-ctext")
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.DELETE)
            element.send_keys(quantity)
            element.send_keys(Keys.ENTER)
            self.wait_for_spinner_to_disappear()
            
        self._open_add_new_charges_modal()
        self._add_job_charges(charges)


    def _open_stock_picker(self) -> None:
        self._debug("Opening Stock Picker modal")
        self.wait_for_spinner_to_disappear()
        self.click(By.XPATH, self.STOCK_PICKER_BUTTON)
        self._wait_for_stock_confirm_button()
        self._wait_for_stock_name_filter_input()

    def _search_stock(self, term: str) -> None:
        self._debug(f"Starting search for stock term: '{term}'")
        self.wait_for_spinner_to_disappear()
        stock_filter = self._wait_for_stock_name_filter_input()
        stock_filter.clear()
        stock_filter.send_keys(term)
        stock_filter.send_keys(Keys.ENTER)
        self._debug("Search text entered; waiting for filtered stock rows")
        self.wait_for_spinner_to_disappear()
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const term = (arguments[0] || "").trim().toLowerCase();
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
                    """,
                    term,
                )
            )
        )

    def _select_matching_stock_row(self, term: str) -> None:
        self._debug(f"Selecting best matching stock row for: {term}")
        self.wait_for_spinner_to_disappear()
        try:
            selected_text = WebDriverWait(self.driver, self.timeout).until(
                lambda d: d.execute_script(
                    """
                    const term = (arguments[0] || "").trim().toLowerCase();
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
                      text: (row.querySelector("td[aria-colindex='1']")?.innerText || row.querySelector("td[aria-colindex='1']")?.textContent || "")
                        .replace(/\\s+/g, " ")
                        .trim()
                    })).filter(entry => entry.text);

                    let target = entries.find(entry => entry.text.toLowerCase() === term) || null;
                    if (!target) {
                      target = entries.find(entry => entry.text.toLowerCase().startsWith(term)) || null;
                    }
                    if (!target) {
                      target = entries.find(entry => entry.text.toLowerCase().includes(term)) || null;
                    }
                    if (!target) return "__NO_MATCH__";

                    const clickable = target.cell || target.node;
                    clickable.scrollIntoView({ block: "center" });
                    clickable.click();
                    return target.text;
                    """,
                    term,
                )
            )
        except TimeoutException:
            selected_text = "__NO_MATCH__"

        if not selected_text or selected_text == "__NO_MATCH__":
            self._cancel_stock_selection()
            raise TimeoutException(
                f"Invalid stock search term '{term}': no matching stock found in Stock Picker"
            )
        self._wait_for_stock_row_selected(selected_text)

    def _wait_for_stock_row_selected(self, selected_text: str) -> None:
        normalized_target = " ".join((selected_text or "").split()).strip().lower()
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const targetText = arguments[0];
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
                    """,
                    normalized_target,
                )
            )
        )

    def _confirm_stock_selection(self) -> None:
        self._debug("Confirming stock selection")

        # 1️⃣ Wait until initial spinner is gone
        self.wait_for_spinner_to_disappear()

        # 2️⃣ Wait until the button is enabled & clickable
        confirm_btn = WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.NAME, "save_stock_details"))
        )

        # 3️⃣ Click the button safely
        try:
            confirm_btn.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", confirm_btn)

        # 4️⃣ Wait for spinner triggered by the click to disappear
        self.wait_for_spinner_to_disappear()

    def _cancel_stock_selection(self) -> None:
        self._debug("Cancelling stock picker because no matching stock was found")
        self.wait_for_spinner_to_disappear()
        cancel_btn = WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.XPATH, self.STOCK_CANCEL_BUTTON))
        )
        try:
            cancel_btn.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", cancel_btn)
        self.wait_for_spinner_to_disappear()
        
    def _open_add_new_charges_modal(self) -> None:
        self._debug("Opening Add New Charges modal from Price Breakup")
        self.wait_for_spinner_to_disappear()
        visible_modals_before = int(
            self.driver.execute_script(
                """
                return Array.from(document.querySelectorAll("div.modal")).filter(m => {
                  const style = window.getComputedStyle(m);
                  return style.display !== "none" && style.visibility !== "hidden";
                }).length;
                """
            )
        )

        self.click(By.XPATH, self.ADD_JOB_CHARGE_BUTTON)
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: int(
                d.execute_script(
                    """
                    return Array.from(document.querySelectorAll("div.modal")).filter(m => {
                      const style = window.getComputedStyle(m);
                      return style.display !== "none" && style.visibility !== "hidden";
                    }).length;
                    """
                )
            )
            > visible_modals_before
        )
        self.wait_for_visible(By.XPATH, self.CHARGES_MODAL)
        self.wait_for_spinner_to_disappear()
        WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.XPATH, self.CHARGES_SEARCH_INPUT))
        )

    def _add_job_charges(self, charges: list[str]) -> None:
        if not charges:
            self._debug("No job charges provided; closing charges modal")
            self.wait_for_spinner_to_disappear()
            self.click(By.XPATH, self.CHARGES_SAVE_BUTTON)
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
        self.click(By.XPATH, self.CHARGES_SAVE_BUTTON)
        self.wait_for_spinner_to_disappear()

    def _select_charge_from_search(self, term: str) -> None:
        print(f"[CHARGE] term={term}")
        self.wait_for_spinner_to_disappear()
        search_input = self._prepare_charges_search_input()
        print("[CHARGE] input focused")
        self._clear_input_value(search_input)
        search_input.send_keys(term)
        print("[CHARGE] sending input term")

        visible_items = WebDriverWait(self.driver, self.timeout).until(
            lambda d: d.execute_script(
                """
                const items = Array.from(document.querySelectorAll(
                  ".k-animation-container .k-item, .k-list .k-item, li.k-item, .k-list-item"
                ));
                return items
                  .filter(item => {
                    const style = window.getComputedStyle(item);
                    return style.display !== "none" && style.visibility !== "hidden" && item.offsetParent !== null;
                  })
                  .map(item => (item.innerText || item.textContent || "").replace(/\\s+/g, " ").trim())
                  .filter(Boolean);
                """
            )
        )
        print(f"[CHARGE] visibleItems={visible_items}")

        selected = WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const term = (arguments[0] || "").trim().toLowerCase();
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

                    let target = normalized.find(entry => entry.text === term)?.node || null;
                    if (!target) {
                      target = normalized.find(entry => entry.text.startsWith(term))?.node || null;
                    }
                    if (!target) {
                      target = normalized.find(entry => entry.text.includes(term))?.node || null;
                    }
                    
                    if (!target) return false;
                    target.scrollIntoView({ block: "center" });
                    target.click();
                    return true;
                    """,
                    term,
                )
            )
        )
        if not selected:
            raise TimeoutException(f"Could not select charge from search: {term}")

        self.wait_for_spinner_to_disappear()
        self._confirm_charge_item(term)

    def _confirm_charge_item(self, term: str) -> None:
        self._debug(f"Confirming selected charge item: {term}")
        confirm_btn = WebDriverWait(self.driver, self.timeout).until(
            lambda d: next(
                (
                    btn
                    for btn in d.find_elements(By.XPATH, self.ADD_CHARGES_BUTTON)
                    if btn.is_displayed()
                    and btn.is_enabled()
                    and btn.get_attribute("disabled") is None
                ),
                None,
            )
        )
        try:
            confirm_btn.click()
        except ElementClickInterceptedException:
            self.wait_for_spinner_to_disappear()
            self.driver.execute_script("arguments[0].click();", confirm_btn)
        self.wait_for_spinner_to_disappear()

    def _get_ready_charges_search_input(self):
        self.wait_for_visible(By.XPATH, self.CHARGES_MODAL)
        self.wait_for_spinner_to_disappear()
        WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.XPATH, self.CHARGES_SEARCH_INPUT))
        )
        return self.wait_for_visible(By.XPATH, self.CHARGES_SEARCH_INPUT)

    def _prepare_charges_search_input(self):
        search_input = self._get_ready_charges_search_input()
        self._focus_click_with_retry(search_input)
        return self._get_ready_charges_search_input()

    def _focus_click_with_retry(self, element, retries: int = 4) -> None:
        last_exc: Exception | None = None
        for _ in range(retries):
            try:
                self.wait_for_spinner_to_disappear()
                element.click()
                return
            except ElementClickInterceptedException as exc:
                last_exc = exc
                self.wait_for_spinner_to_disappear()
                self.driver.execute_script(
                    "arguments[0].focus(); arguments[0].click();", element
                )
                return
        if last_exc is not None:
            raise last_exc

    def _clear_input_value(self, element) -> None:
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        try:
            element.clear()
        except Exception:
            pass

    def _wait_for_stock_confirm_button(self):
        return WebDriverWait(self.driver, self.timeout).until(
            lambda d: next(
                (
                    b
                    for b in d.find_elements(By.XPATH, self.STOCK_CONFIRM_BUTTON)
                    if b.is_displayed()
                ),
                None,
            )
        )

    def _wait_for_stock_name_filter_input(self):
        element = WebDriverWait(self.driver, self.timeout).until(
            lambda d: d.execute_script(
                """
                const btn = Array.from(document.querySelectorAll("button[name='save_stock_details']"))
                  .find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                if (!btn) return null;
                const modalRoot = btn.closest(".modal-content") || btn.closest(".modal") || document;

                const headers = Array.from(modalRoot.querySelectorAll("th[role='columnheader']"));
                const stockHeader = headers.find(th =>
                  (th.innerText || th.textContent || "").replace(/\s+/g, " ").trim().toLowerCase().includes("stock name")
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
                """
            )
        )
        if element is None:
            raise TimeoutException(
                "Could not find Stock Name filter input in stock modal"
            )
        return element
