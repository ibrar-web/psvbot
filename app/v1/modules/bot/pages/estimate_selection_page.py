import logging

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimateSelectionPage(BasePage):
    """Page for searching and selecting an existing estimate on the quick-access page."""

    # The category label/button that opens the module dropdown (shows current selection e.g. "Invoice")
    MODULE_CATEGORIES_DIV = "xpath=//div[contains(@class,'dot-search-categories')]"
    # The hidden <select> inside the categories div
    MODULE_SELECT = "xpath=//select[@name='module_select' or @id='module_select']"
    SEARCH_INPUT = "xpath=//input[@name='module_search_feild']"
    SEARCH_RESULTS = "xpath=//div[contains(@class,'search-results')]//a[contains(@class,'search-item')]"
    LOCKED_DIALOG_TIMEOUT_MS = 5000

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimateSelection] {message}")
        logger.info(message)

    def _select_estimate_module(self) -> None:
        """Click the module category button and select 'Estimate' (value=2) from the dropdown."""
        self._debug("Selecting 'Estimate' from module dropdown")

        # Click the categories div to reveal/activate the select
        self.wait_for_visible(self.MODULE_CATEGORIES_DIV)
        self.click(self.MODULE_CATEGORIES_DIV)

        # Use JS to set the select value to 2 (Estimate) and fire change event
        self.page.evaluate(
            """() => {
                const sel = document.querySelector(
                    'select[name="module_select"], select#module_select'
                );
                if (!sel) return;
                sel.value = '2';
                sel.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        self.wait_for_spinner_to_disappear()

        # Verify the label now shows "Estimate"
        label_text = self.page.evaluate(
            """() => {
                const label = document.querySelector(
                    'span[name="selected_module_label"]'
                );
                return label ? (label.textContent || '').trim() : '';
            }"""
        )
        self._debug(f"Module label after selection: '{label_text}'")

    def search_and_open_estimate(self, estimate_id: str) -> None:
        """Search for an existing estimate by ID and open it.

        Steps:
        1. Click the module category button and select 'Estimate' from the dropdown
        2. Type the estimate_id into the search field and press Enter
        3. If a "not found" warning appears, raise RuntimeError
        4. Otherwise wait for the estimate record to open
        """
        self._debug(f"Searching for existing estimate_id={estimate_id}")
        self.wait_for_spinner_to_disappear()

        # Step 1: Select 'Estimate' from the module dropdown
        self._select_estimate_module()

        # Step 2: Type the estimate_id into the search input and press Enter
        self.wait_for_visible(self.SEARCH_INPUT)
        self.type(self.SEARCH_INPUT, str(estimate_id), clear_first=True)
        self._debug(f"Entered estimate_id '{estimate_id}' in search field")
        self.page.keyboard.press("Enter")
        self.wait_for_spinner_to_disappear()
        self._debug("Enter pressed, waiting for result or not-found warning")

        # Step 3: Wait for either a not-found warning or the estimate record to open
        outcome = self.page.wait_for_function(
            """() => {
                // Check for a ui-dialog with title "Warning" (visible)
                const warningDialog = Array.from(document.querySelectorAll(".ui-dialog")).find(dialog => {
                    const style = window.getComputedStyle(dialog);
                    if (style.display === "none" || style.visibility === "hidden") return false;
                    const title = (
                        dialog.querySelector(".ui-dialog-title")?.innerText || ""
                    ).trim().toLowerCase();
                    return title === "warning";
                });
                if (warningDialog) return "not_found";

                // Check if estimate tabs have appeared (record opened)
                const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                const opened = tabs.some(tab => {
                    const text = (tab.innerText || tab.textContent || "")
                        .replace(/\\s+/g, " ").trim();
                    return text.includes("Estimate Summary") || text.includes("Job Details");
                });
                if (opened || window.location.href.includes("#/invoicing/invoice-page")) {
                    return "opened";
                }

                return false;
            }""",
            timeout=self._timeout_ms,
        ).json_value()

        if outcome == "not_found":
            self._dismiss_warning_dialog()
            raise RuntimeError(f"Estimate not found: estimate_id={estimate_id}")

        self._debug(f"Clicked search result for estimate_id={estimate_id}")
        self.wait_for_spinner_to_disappear()
        outcome = self._wait_for_estimate_opened_or_locked()
        if outcome == "locked":
            if not self._dismiss_locked_estimate_dialog_if_present():
                raise RuntimeError(
                    f"Estimate {estimate_id} is locked and the OK dialog could not be dismissed"
                )

            if self._dismiss_locked_estimate_dialog_if_present():
                raise RuntimeError(
                    f"Estimate {estimate_id} is still locked after the second lock dialog; stopping flow"
                )

            self._wait_for_estimate_opened_or_locked(expect_locked=False)
        self._debug("Estimate record opened, spinner dismissed")

    def _dismiss_warning_dialog(self) -> None:
        """Close the visible Warning dialog and wait for it to disappear before returning."""
        try:
            self.page.evaluate(
                """() => {
                    const isVisible = el => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && parseFloat(style.opacity || "1") > 0;
                    };
                    const dialog = Array.from(document.querySelectorAll(".ui-dialog")).find(d => {
                        if (!isVisible(d)) return false;
                        const title = (d.querySelector(".ui-dialog-title")?.innerText || "")
                            .trim().toLowerCase();
                        return title === "warning";
                    });
                    if (!dialog) return;
                    const closeBtn = dialog.querySelector(".ui-dialog-titlebar-close, button.ui-dialog-titlebar-icon");
                    if (closeBtn && isVisible(closeBtn)) { closeBtn.click(); return; }
                    const btn = Array.from(dialog.querySelectorAll("button")).find(b => {
                        const label = (b.innerText || b.textContent || "").trim().toLowerCase();
                        return label === "ok" || label === "close";
                    });
                    if (btn) btn.click();
                }"""
            )
            # Wait until the Warning dialog is gone from the DOM / hidden
            self.page.wait_for_function(
                """() => {
                    const isVisible = el => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && parseFloat(style.opacity || "1") > 0;
                    };
                    return !Array.from(document.querySelectorAll(".ui-dialog")).some(d => {
                        if (!isVisible(d)) return false;
                        const title = (d.querySelector(".ui-dialog-title")?.innerText || "")
                            .trim().toLowerCase();
                        return title === "warning";
                    });
                }""",
                timeout=5000,
            )
            self._debug("Warning dialog dismissed")
        except Exception:
            self._debug("Warning dialog dismiss timed out or failed — continuing anyway")

    def _wait_for_estimate_opened_or_locked(self, *, expect_locked: bool = True) -> str:
        try:
            outcome = self.page.wait_for_function(
                """(expectLocked) => {
                    const isVisible = el => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && parseFloat(style.opacity || "1") > 0
                            && el.getClientRects().length > 0;
                    };

                    if (expectLocked) {
                        const lockedDialog = Array.from(document.querySelectorAll(
                            ".ui-confirmdialog.ui-dialog, .ui-confirmdialog, .ui-dialog"
                        )).find(dialog => {
                            if (!isVisible(dialog)) return false;
                            const message = (
                                dialog.querySelector(".ui-confirmdialog-message")?.innerText
                                || dialog.innerText
                                || ""
                            ).replace(/\\s+/g, " ").trim().toLowerCase();
                            return message.includes("locked by user");
                        });
                        if (lockedDialog) return "locked";
                    }

                    const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                    const hasInvoiceTabs = tabs.some(tab => {
                        const text = (tab.innerText || tab.textContent || "")
                            .replace(/\\s+/g, " ")
                            .trim();
                        return text.includes("Estimate Summary") || text.includes("Job Details");
                    });
                    if (hasInvoiceTabs || window.location.href.includes("#/invoicing/invoice-page")) {
                        return "opened";
                    }

                    return false;
                }""",
                arg=expect_locked,
                timeout=self._timeout_ms,
            ).json_value()
            self._debug(f"Estimate selection outcome: {outcome}")
            return str(outcome)
        except PlaywrightTimeoutError as exc:
            raise PlaywrightTimeoutError(
                "Timed out waiting for selected estimate to open"
            ) from exc

    def _dismiss_locked_estimate_dialog_if_present(self) -> bool:
        try:
            has_locked_dialog = self.page.wait_for_function(
                """() => {
                    const isVisible = el => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && parseFloat(style.opacity || "1") > 0
                            && el.getClientRects().length > 0;
                    };
                    return Array.from(document.querySelectorAll(
                        ".ui-confirmdialog.ui-dialog, .ui-confirmdialog, .ui-dialog"
                    )).some(dialog => {
                        if (!isVisible(dialog)) return false;
                        const message = (
                            dialog.querySelector(".ui-confirmdialog-message")?.innerText
                            || dialog.innerText
                            || ""
                        ).replace(/\\s+/g, " ").trim().toLowerCase();
                        return message.includes("locked by user");
                    });
                }""",
                timeout=self.LOCKED_DIALOG_TIMEOUT_MS,
            ).json_value()
        except PlaywrightTimeoutError:
            return False

        if not has_locked_dialog:
            return False

        clicked = self.page.evaluate(
            """() => {
                const isVisible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && parseFloat(style.opacity || "1") > 0
                        && el.getClientRects().length > 0;
                };
                const dialog = Array.from(document.querySelectorAll(
                    ".ui-confirmdialog.ui-dialog, .ui-confirmdialog, .ui-dialog"
                )).find(node => {
                    if (!isVisible(node)) return false;
                    const message = (
                        node.querySelector(".ui-confirmdialog-message")?.innerText
                        || node.innerText
                        || ""
                    ).replace(/\\s+/g, " ").trim().toLowerCase();
                    return message.includes("locked by user");
                });
                if (!dialog) return false;
                const okButton = Array.from(dialog.querySelectorAll("button[pbutton], button"))
                    .find(button => {
                        if (button.disabled || !isVisible(button)) return false;
                        const label = (
                            button.querySelector(".ui-button-text")?.innerText
                            || button.innerText
                            || button.textContent
                            || ""
                        ).replace(/\\s+/g, " ").trim().toLowerCase();
                        return label === "ok";
                    });
                if (!okButton) return false;
                okButton.click();
                return true;
            }"""
        )
        if not clicked:
            return False

        self._debug("Locked estimate dialog detected; clicked OK")
        try:
            self.page.wait_for_function(
                """() => {
                    const isVisible = el => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== "none"
                            && style.visibility !== "hidden"
                            && parseFloat(style.opacity || "1") > 0
                            && el.getClientRects().length > 0;
                    };
                    return !Array.from(document.querySelectorAll(
                        ".ui-confirmdialog.ui-dialog, .ui-confirmdialog, .ui-dialog"
                    )).some(dialog => {
                        if (!isVisible(dialog)) return false;
                        const message = (
                            dialog.querySelector(".ui-confirmdialog-message")?.innerText
                            || dialog.innerText
                            || ""
                        ).replace(/\\s+/g, " ").trim().toLowerCase();
                        return message.includes("locked by user");
                    });
                }""",
                timeout=self.LOCKED_DIALOG_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            self._debug("Locked estimate dialog did not disappear after OK")
        self.wait_for_spinner_to_disappear()
        return True
