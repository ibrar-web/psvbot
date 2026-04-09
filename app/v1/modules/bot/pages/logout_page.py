import logging

from selenium.common.exceptions import NoAlertPresentException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class LogoutPage(BasePage):
    USER_OPTIONS_DROPDOWN = (
        "//span[@name='user-options-dropdown-container']"
        " | //*[@name='user-options-dropdown']"
    )
    LOGOUT_LINK = "//a[@name='user-options-logout']"
    LEAVE_BUTTON = (
        "//button[normalize-space()='Leave']"
        " | //a[normalize-space()='Leave']"
        " | //span[normalize-space()='Leave']/ancestor::button[1]"
    )

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][LogoutPage] {message}")
            logger.info(message)

    def logout(self) -> None:
        self._debug("Opening user options dropdown")
        self.click(By.XPATH, self.USER_OPTIONS_DROPDOWN)
        self._debug("Clicking logout")
        self.click(By.XPATH, self.LOGOUT_LINK)
        self._handle_leave_confirmation()

    def _handle_leave_confirmation(self) -> None:
        self._debug("Checking for 'Leave site?' confirmation")
        try:
            alert = WebDriverWait(self.driver, 2).until(lambda d: d.switch_to.alert)
            _ = alert.text
            alert.accept()
            self._debug("Browser alert detected; accepted")
            return
        except (NoAlertPresentException, TimeoutException):
            pass

        if self.is_visible(By.XPATH, self.LEAVE_BUTTON):
            self._debug("Modal 'Leave' button detected; clicking")
            self.click(By.XPATH, self.LEAVE_BUTTON)
