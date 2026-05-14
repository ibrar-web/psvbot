import logging

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class BasePage:

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
