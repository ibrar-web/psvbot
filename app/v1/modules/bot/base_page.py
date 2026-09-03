import logging

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class BasePage:

    # Shared user-options top-nav menu (also used by LogoutPage) and the
    # "Record Lock" admin panel reachable from it. Any page can hit a
    # "record is locked" error, so this lives here rather than on one
    # specific page object.
    USER_OPTIONS_DROPDOWN = (
        "xpath=//span[@name='user-options-dropdown-container']"
        " | //*[@name='user-options-dropdown']"
    )
    RECORD_LOCK_MENU_ITEM = "xpath=//a[@name='record_lock']"
    RECORD_LOCK_MODAL_CLOSE = "xpath=//span[@name='close_record_lock_popup']"
    RECORD_LOCK_SCOPE_DROPDOWN = (
        "xpath=//label[.//span[contains(normalize-space(),'Locked Records of')]]"
        "/following-sibling::div[1]//kendo-dropdownlist"
    )
    RECORD_LOCK_UNLOCK_BUTTON = "xpath=//input[@type='button' and @value='Unlock']"

    def __init__(self, page: Page, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.page = page
        self.timeout = timeout
        self._timeout_ms = timeout * 1000
        self._warning_observer_active = False

    def start_warning_auto_dismiss(self) -> None:
        """
        Inject a MutationObserver into the page that continuously watches for
        PrimeNG warning/confirm dialogs and auto-clicks their affirmative button
        the moment they appear in the DOM. Safe to call multiple times — only
        installs once per page session.
        """
        if self._warning_observer_active:
            return

        self.page.evaluate(
            """() => {
                if (window.__warningObserverActive) return;
                window.__warningObserverActive = true;

                const AFFIRMATIVE = [
                    'ok', 'yes', 'continue', 'next', 'close',
                    'got it', 'confirm', 'accept', 'proceed', 'done'
                ];

                function isVisible(el) {
                    const s = window.getComputedStyle(el);
                    return s.display !== 'none'
                        && s.visibility !== 'hidden'
                        && parseFloat(s.opacity || '1') > 0
                        && el.offsetWidth > 0
                        && el.offsetHeight > 0;
                }

                function dismissAll() {
                    const dialogs = Array.from(document.querySelectorAll(
                        '.ui-confirmdialog, .ui-dialog, [role="dialog"], [role="alertdialog"], .modal-content'
                    )).filter(isVisible);

                    let dismissed = 0;
                    for (const dialog of dialogs) {
                        const btn = Array.from(dialog.querySelectorAll('button[pbutton], button')).find(b => {
                            if (b.disabled) return false;
                            const s = window.getComputedStyle(b);
                            if (s.display === 'none' || s.visibility === 'hidden') return false;
                            const label = (
                                b.querySelector('.ui-button-text')?.innerText ||
                                b.querySelector('.ui-button-text')?.textContent ||
                                b.innerText || b.textContent || ''
                            ).trim().toLowerCase();
                            return AFFIRMATIVE.some(a => label === a || label.startsWith(a));
                        });

                        if (btn) {
                            console.log('[AutoDismiss] Dismissing warning dialog');
                            btn.click();
                            dismissed++;
                        }
                    }
                    return dismissed;
                }

                // MutationObserver: catches dialogs added to the DOM
                const observer = new MutationObserver(mutations => {
                    const hasAdditions = mutations.some(m => m.addedNodes.length > 0);
                    if (hasAdditions) dismissAll();
                });
                observer.observe(document.body, { childList: true, subtree: true });
                window.__warningObserver = observer;

                // setInterval poll: catches dialogs that appear via CSS animation
                // (already in DOM but shown by opacity/transform change — no DOM mutation)
                window.__warningInterval = setInterval(() => {
                    dismissAll();
                }, 400);
            }"""
        )
        self._warning_observer_active = True
        logger.info("start_warning_auto_dismiss: MutationObserver installed")

    def stop_warning_auto_dismiss(self) -> None:
        """Disconnect the MutationObserver and reset the flag."""
        if not self._warning_observer_active:
            return
        self.page.evaluate(
            """() => {
                if (window.__warningObserver) {
                    window.__warningObserver.disconnect();
                }
                if (window.__warningInterval) {
                    clearInterval(window.__warningInterval);
                    window.__warningInterval = null;
                }
                window.__warningObserverActive = false;
            }"""
        )
        self._warning_observer_active = False
        logger.info("stop_warning_auto_dismiss: MutationObserver disconnected")

    # ------------------------------------------------------------------
    # Locator helpers
    # ------------------------------------------------------------------

    def _loc(self, selector: str):
        """Return a Playwright locator. Accepts xpath=... or css selectors."""
        return self.page.locator(selector)

    def _xpath(self, xpath: str):
        return self.page.locator(f"xpath={xpath}")

    # ------------------------------------------------------------------
    # Waiting helpers
    # ------------------------------------------------------------------

    def wait_for_visible(self, selector: str) -> None:
        self._loc(selector).first.wait_for(state="visible", timeout=self._timeout_ms)

    def wait_for_clickable(self, selector: str) -> None:
        self._loc(selector).first.wait_for(state="visible", timeout=self._timeout_ms)

    def wait_for_invisible(self, selector: str) -> None:
        self._loc(selector).first.wait_for(state="hidden", timeout=self._timeout_ms)

    def find(self, selector: str):
        self.wait_for_visible(selector)
        return self._loc(selector).first

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def click(self, selector: str) -> None:
        self.wait_for_spinner_to_disappear()
        locator = self._loc(selector).first
        locator.wait_for(state="visible", timeout=self._timeout_ms)
        try:
            locator.click(timeout=self._timeout_ms)
        except PlaywrightTimeoutError:
            # Fallback: JS click for stubborn elements (e.g. <input type="button">)
            locator.evaluate("el => el.click()")

    def type(self, selector: str, value: str, clear_first: bool = True) -> None:
        self.wait_for_spinner_to_disappear()
        locator = self._loc(selector).first
        locator.wait_for(state="visible", timeout=self._timeout_ms)
        if clear_first:
            locator.fill(value, timeout=self._timeout_ms)
        else:
            locator.type(value, timeout=self._timeout_ms)
        self.wait_for_spinner_to_disappear()

    def is_visible(self, selector: str) -> bool:
        try:
            return self._loc(selector).first.is_visible()
        except Exception:
            return False

    def type_if_visible(self, selector: str, value: str, clear_first: bool = True) -> bool:
        if not self.is_visible(selector):
            return False
        self.type(selector, value, clear_first=clear_first)
        return True

    # ------------------------------------------------------------------
    # Spinner / progress-bar waits
    # ------------------------------------------------------------------

    def wait_for_spinner_to_disappear(self) -> None:
        self.page.wait_for_function(
            """() => {
                const overlay = document.querySelector('.spinner-overlay');
                const progress = document.querySelector('.ng-progress');
                const overlayHidden = !overlay || window.getComputedStyle(overlay).display === 'none';
                const progressInactive = !progress || !progress.classList.contains('active');
                return overlayHidden && progressInactive;
            }""",
            timeout=self._timeout_ms,
        )

    def _nudge_mouse(self) -> None:
        """Move the real mouse cursor to reset a lingering hover/busy state.

        Observed live on the Estimate History grid: after a reload (e.g.
        clear filters), the next click (Download as CSV) can hang
        indefinitely even though wait_for_spinner_to_disappear() already
        passed — Playwright's click() waits for the target to receive
        pointer events, but something from the reload keeps intercepting
        them at the cursor's last position. Manually moving the mouse
        anywhere on screen unblocks it every time; this reproduces that
        instead of relying on a human being at the keyboard.
        """
        viewport = self.page.viewport_size or {"width": 1280, "height": 720}
        self.page.mouse.move(10, 10)
        self.page.mouse.move(viewport["width"] // 2, viewport["height"] // 2)

    # ------------------------------------------------------------------
    # Kendo combobox helper
    # ------------------------------------------------------------------

    def wait_for_kendo_combobox_search_to_settle(self, xpath_locator: str) -> None:
        self.page.wait_for_function(
            """(xpathLocator) => {
                const input = document.evaluate(
                  xpathLocator,
                  document,
                  null,
                  XPathResult.FIRST_ORDERED_NODE_TYPE,
                  null
                ).singleNodeValue;
                if (!input) return false;
                const combo = input.closest('kendo-combobox');
                if (!combo) return false;
                const icon = combo.querySelector('.k-select .k-icon');
                if (!icon) return false;
                const className = icon.className || '';
                const isLoading = className.includes('k-i-loading');
                const isReady = className.includes('k-i-arrow-s');
                return !isLoading && isReady;
            }""",
            arg=xpath_locator,
            timeout=self._timeout_ms,
        )

    # ------------------------------------------------------------------
    # Record lock handling — shared across any page that can hit a
    # "record is locked by user X" error (Estimate History lookup today,
    # potentially create-estimate or other flows later).
    # ------------------------------------------------------------------

    def dismiss_locked_record_dialog_if_present(self, timeout_ms: int = 5000) -> bool:
        """Dismiss every 'Estimate N is locked by user X' error dialog
        currently visible — PrintSmith can stack more than one of these on
        top of each other (one per locked estimate). Loops clicking OK until
        none remain, so a caller never proceeds while one is still covering
        the page (which silently swallows subsequent clicks).

        Returns True if at least one dialog was present (and all are now
        dismissed).
        """
        try:
            has_dialog = self.page.wait_for_function(
                self._LOCKED_DIALOG_EXISTS_JS,
                timeout=timeout_ms,
            ).json_value()
        except PlaywrightTimeoutError:
            return False

        if not has_dialog:
            return False

        for _ in range(10):  # safety cap against a runaway loop
            dismissed = self.page.evaluate(self._DISMISS_ONE_LOCKED_DIALOG_JS)
            if not dismissed:
                break
            self.page.wait_for_timeout(300)

        try:
            self.page.wait_for_function(
                self._LOCKED_DIALOG_ABSENT_JS,
                timeout=timeout_ms,
            )
        except PlaywrightTimeoutError:
            logger.warning(
                "dismiss_locked_record_dialog_if_present: a locked-record "
                "dialog may still be visible after dismiss attempts"
            )

        self.wait_for_spinner_to_disappear()
        logger.info("dismiss_locked_record_dialog_if_present: dismissed locked-record dialog(s)")
        return True

    _LOCKED_DIALOG_EXISTS_JS = """() => {
        const isVisible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            return style.display !== "none" && style.visibility !== "hidden"
                && el.getClientRects().length > 0;
        };
        return Array.from(document.querySelectorAll(
            ".ui-dialog.ui-confirmdialog, .ui-confirmdialog"
        )).some(dialog => {
            if (!isVisible(dialog)) return false;
            const message = (
                dialog.querySelector(".ui-confirmdialog-message")?.innerText || ""
            ).toLowerCase();
            return message.includes("is locked by user");
        });
    }"""

    _LOCKED_DIALOG_ABSENT_JS = """() => {
        const isVisible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            return style.display !== "none" && style.visibility !== "hidden"
                && el.getClientRects().length > 0;
        };
        return !Array.from(document.querySelectorAll(
            ".ui-dialog.ui-confirmdialog, .ui-confirmdialog"
        )).some(dialog => {
            if (!isVisible(dialog)) return false;
            const message = (
                dialog.querySelector(".ui-confirmdialog-message")?.innerText || ""
            ).toLowerCase();
            return message.includes("is locked by user");
        });
    }"""

    _DISMISS_ONE_LOCKED_DIALOG_JS = """() => {
        const isVisible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            return style.display !== "none" && style.visibility !== "hidden"
                && el.getClientRects().length > 0;
        };
        const dialog = Array.from(document.querySelectorAll(
            ".ui-dialog.ui-confirmdialog, .ui-confirmdialog"
        )).find(d => {
            if (!isVisible(d)) return false;
            const message = (
                d.querySelector(".ui-confirmdialog-message")?.innerText || ""
            ).toLowerCase();
            return message.includes("is locked by user");
        });
        if (!dialog) return false;
        const okBtn = Array.from(dialog.querySelectorAll("button")).find(b => {
            const label = (
                b.querySelector(".ui-button-text")?.innerText || b.innerText || ""
            ).trim().toLowerCase();
            return label === "ok";
        });
        if (!okBtn) return false;
        okBtn.click();
        return true;
    }"""

    def unlock_all_records(self) -> None:
        """Open the 'Record Lock' admin panel (user-options menu -> Record
        Lock), switch scope to all users, and release every listed lock.
        """
        logger.info("unlock_all_records: opening user options menu")
        self.click(self.USER_OPTIONS_DROPDOWN)
        self.click(self.RECORD_LOCK_MENU_ITEM)
        self.wait_for_spinner_to_disappear()

        logger.info("unlock_all_records: switching scope to all users")
        self._select_record_lock_scope_all_users()
        self.wait_for_spinner_to_disappear()

        logger.info("unlock_all_records: clicking Unlock")
        self.click(self.RECORD_LOCK_UNLOCK_BUTTON)
        self.wait_for_spinner_to_disappear()

        if self.is_visible(self.RECORD_LOCK_MODAL_CLOSE):
            logger.info("unlock_all_records: closing Record Lock modal")
            self.click(self.RECORD_LOCK_MODAL_CLOSE)
            self.wait_for_spinner_to_disappear()

    # Same scoped-to-popup selector pattern already proven for kendo-dropdownlist
    # elsewhere in this codebase (JobDetailsTab.select_vendor) — a bare
    # ".k-list-item" with no ancestor scoping matches unrelated elements
    # anywhere on the page (observed live: it matched a grid column header).
    _KENDO_POPUP_ITEMS_JS = (
        ".k-animation-container [role='listbox'] li.k-item, "
        ".k-animation-container li.k-item, "
        ".k-list [role='option'], .k-list li.k-item"
    )

    def _select_record_lock_scope_all_users(self) -> None:
        # Two earlier approaches both kept landing on "User Name" (an actual
        # grid column name, not a real dropdown option): querying the popup
        # DOM by CSS proximity, then driving via keyboard. Guessing at CSS
        # scoping has failed twice, so this scopes via the widget's own
        # accessibility wiring instead: Kendo's dropdownlist input carries
        # aria-owns/aria-controls pointing at the exact popup listbox id it
        # opened, which is unambiguous regardless of how many other Kendo
        # popups exist on the page.
        dropdown_loc = self._loc(self.RECORD_LOCK_SCOPE_DROPDOWN).first
        dropdown_loc.wait_for(state="visible", timeout=self._timeout_ms)
        dropdown_loc.scroll_into_view_if_needed(timeout=self._timeout_ms)

        opener_loc = dropdown_loc.locator(".k-input, .k-select, .k-dropdown-wrap").first

        def displayed_text() -> str:
            return (
                dropdown_loc.evaluate(
                    "(dropdown) => (dropdown.querySelector('.k-input')?.innerText || '').trim()"
                )
                or ""
            )

        before = displayed_text()
        logger.info("_select_record_lock_scope_all_users: before='%s'", before)

        opener_loc.click(timeout=self._timeout_ms)

        try:
            self.page.wait_for_function(
                """(dropdown) => {
                    const owner = dropdown.querySelector("[aria-owns], [aria-controls]");
                    if (!owner) return false;
                    const id = owner.getAttribute("aria-owns") || owner.getAttribute("aria-controls");
                    const popup = id && document.getElementById(id);
                    return !!(popup && popup.querySelectorAll("li, [role='option']").length > 0);
                }""",
                arg=dropdown_loc.element_handle(timeout=self._timeout_ms),
                timeout=self._timeout_ms,
            )
        except PlaywrightTimeoutError:
            logger.warning("_select_record_lock_scope_all_users: popup listbox did not resolve via aria-owns/aria-controls")

        items = dropdown_loc.evaluate(
            """(dropdown) => {
                const owner = dropdown.querySelector("[aria-owns], [aria-controls]");
                const id = owner && (owner.getAttribute("aria-owns") || owner.getAttribute("aria-controls"));
                const popup = id && document.getElementById(id);
                if (!popup) return [];
                return Array.from(popup.querySelectorAll("li, [role='option']"))
                    .map(node => (node.innerText || node.textContent || "").trim())
                    .filter(Boolean);
            }"""
        )
        logger.info("_select_record_lock_scope_all_users: popup items=%s", items)

        clicked = dropdown_loc.evaluate(
            """(dropdown) => {
                const owner = dropdown.querySelector("[aria-owns], [aria-controls]");
                const id = owner && (owner.getAttribute("aria-owns") || owner.getAttribute("aria-controls"));
                const popup = id && document.getElementById(id);
                if (!popup) return null;
                const candidates = Array.from(popup.querySelectorAll("li, [role='option']"));
                const target = candidates.find(node => {
                    const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                    return text.includes("all");
                }) || candidates.find(node => {
                    const text = (node.innerText || node.textContent || "").trim().toLowerCase();
                    return !text.includes("currently logged in") && !text.includes("yourself");
                });
                if (!target) return null;
                target.scrollIntoView({ block: "center" });
                target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
                target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
                return (target.innerText || target.textContent || "").trim();
            }"""
        )
        self.page.wait_for_timeout(300)
        after = displayed_text()
        logger.info("_select_record_lock_scope_all_users: clicked='%s' after='%s'", clicked, after)
