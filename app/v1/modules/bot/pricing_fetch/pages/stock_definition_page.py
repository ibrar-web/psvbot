import logging
from typing import Any, Dict, List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage

logger = logging.getLogger(__name__)

GRID_LOAD_TIMEOUT_MS = 15_000
GRID_REFRESH_TIMEOUT_MS = 5_000

READ_ROWS_SCRIPT = """
rows => rows.map(row => {
    const cellText = id => {
        const cell = row.querySelector(`td#col_data_${id}`);
        return cell ? cell.textContent.trim() : '';
    };
    return {
        name: cellText('name'),
        size: cellText('size'),
        price: cellText('cost1'),
    };
})
"""

# Row count plus first/last names, used to detect when the grid reloads.
GRID_SIGNATURE_SCRIPT = """
selector => {
    const names = [...document.querySelectorAll(selector)]
        .map(row => row.textContent.trim().slice(0, 200));
    return `${names.length}|${names[0]}|${names[names.length - 1]}`;
}
"""

HAS_ACTIVE_FILTER_SCRIPT = """
() => [...document.querySelectorAll("input[name^='filter_']")]
    .some(input => input.value.trim() !== '')
"""


class StockDefinitionPage(BasePage):
    INVENTORY_STOCK_TOGGLE = "label[name='inv_stocklabel']"
    INVENTORY_STOCK_CHECKBOX = (
        "label[name='inv_stocklabel'] input[type='checkbox']"
    )
    CLEAR_ALL_FILTERS_BUTTON = "button[name='reset_estimate_history_grid']"
    STOCK_ROWS = "table.ui-table-scrollable-body-table tr.ui-selectable-row"
    # A hidden paginator sits earlier in the DOM, so only the visible one.
    NEXT_PAGE_BUTTON = "a.ui-paginator-next:visible"

    def wait_for_grid(self) -> None:
        self.wait_for_visible(self.INVENTORY_STOCK_TOGGLE)
        try:
            self._loc(self.STOCK_ROWS).first.wait_for(
                state="attached", timeout=GRID_LOAD_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            logger.warning("Stock grid loaded without any rows")

    def clear_filters(self) -> None:
        # PSV remembers column filters per user between sessions.
        if not self.page.evaluate(HAS_ACTIVE_FILTER_SCRIPT):
            logger.info("No saved stock grid filters")
            return

        signature = self._grid_signature()
        self.click(self.CLEAR_ALL_FILTERS_BUTTON)
        self._wait_for_grid_refresh(signature)
        logger.info("Cleared saved stock grid filters")

    def enable_inventory_stock_filter(self) -> None:
        if self._loc(self.INVENTORY_STOCK_CHECKBOX).first.is_checked():
            logger.info("Inventory Stock filter already enabled")
            return

        signature = self._grid_signature()
        self.click(self.INVENTORY_STOCK_TOGGLE)
        self._wait_for_grid_refresh(signature)
        logger.info("Inventory Stock filter enabled")

    def read_stock_rows(self) -> List[Dict[str, Any]]:
        all_rows = self._read_current_page()
        page_number = 1
        while self._has_next_page():
            page_number += 1
            self._go_to_next_page()
            all_rows.extend(self._read_current_page())
        logger.info(
            "Read %s stock rows from %s pages", len(all_rows), page_number
        )
        return all_rows

    def _read_current_page(self) -> List[Dict[str, Any]]:
        rows = self._loc(self.STOCK_ROWS).evaluate_all(READ_ROWS_SCRIPT)
        logger.info("Read %s stock rows from current page", len(rows))
        return rows

    def _has_next_page(self) -> bool:
        next_button = self._loc(self.NEXT_PAGE_BUTTON)
        if not next_button.count():
            return False
        button_classes = next_button.first.get_attribute("class") or ""
        return "ui-state-disabled" not in button_classes

    def _go_to_next_page(self) -> None:
        signature = self._grid_signature()
        self.click(self.NEXT_PAGE_BUTTON)
        self._wait_for_grid_refresh(signature, required=True)

    def _grid_signature(self) -> str:
        return self.page.evaluate(GRID_SIGNATURE_SCRIPT, self.STOCK_ROWS)

    def _wait_for_grid_refresh(
        self, previous_signature: str, required: bool = False
    ) -> None:
        # The grid reloads after a click without always showing a spinner,
        # so wait for the rows themselves to change.
        try:
            self.page.wait_for_function(
                f"([selector, previous]) => "
                f"({GRID_SIGNATURE_SCRIPT})(selector) !== previous",
                arg=[self.STOCK_ROWS, previous_signature],
                timeout=self._timeout_ms if required else (
                    GRID_REFRESH_TIMEOUT_MS
                ),
            )
        except PlaywrightTimeoutError:
            if required:
                raise
            logger.info("Stock grid rows did not change")
        self.wait_for_spinner_to_disappear()
