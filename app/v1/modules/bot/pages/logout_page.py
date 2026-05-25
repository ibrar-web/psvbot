import logging

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class LogoutPage(BasePage):
    USER_OPTIONS_DROPDOWN = (
        "xpath=//span[@name='user-options-dropdown-container']"
        " | //*[@name='user-options-dropdown']"
    )
    LOGOUT_LINK = "xpath=//a[@name='user-options-logout']"
    LEAVE_BUTTON = (
        "xpath=//button[normalize-space()='Leave']"
        " | //a[normalize-space()='Leave']"
        " | //span[normalize-space()='Leave']/ancestor::button[1]"
    )

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][LogoutPage] {message}")
            logger.info(message)

    def logout(self) -> None:
        self._debug("Opening user options dropdown")
        self.click(self.USER_OPTIONS_DROPDOWN)
        self._debug("Clicking logout")
        self.click(self.LOGOUT_LINK)
        self._handle_leave_confirmation()

    def _handle_leave_confirmation(self) -> None:
        self._debug("Checking for 'Leave site?' confirmation")

        # Handle browser-level dialog (beforeunload alert)
        try:
            with self.page.expect_event("dialog", timeout=2000) as dialog_info:
                pass
            dialog = dialog_info.value
            self._debug(f"Browser dialog detected: {dialog.message}")
            dialog.accept()
            return
        except PlaywrightTimeoutError:
            pass

        # Handle in-page modal Leave button
        if self.is_visible(self.LEAVE_BUTTON):
            self._debug("Modal 'Leave' button detected; clicking")
            self.click(self.LEAVE_BUTTON)
