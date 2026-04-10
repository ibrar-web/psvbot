import logging

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class LoginPage(BasePage):
    USERNAME_INPUT = "xpath=//input[@name='userName' or contains(@id,'user')]"
    PASSWORD_INPUT = "xpath=//input[@type='password' or @name='password' or contains(@id,'password')]"
    COMPANY_INPUT = "xpath=//input[@name='companyName' or contains(@id,'company')]"
    LOGIN_BUTTON_ID = "#loginBtn"
    LOGIN_BUTTON = (
        "xpath=//input[@id='loginBtn' and @type='button']"
        " | //input[contains(@onclick,'validateLogin') and @type='button']"
        " | //input[@type='button' and translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='login']"
        " | //button[@type='submit' or contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]"
        " | //input[@type='submit']"
    )

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][LoginPage] {message}")
            logger.info(message)

    def login(self, username: str, password: str, company: str) -> None:
        self._debug("Filling username/password fields")
        self.type(self.USERNAME_INPUT, username)
        self.type(self.PASSWORD_INPUT, password)
        if company:
            self._debug("Filling company field")
            self.type_if_visible(self.COMPANY_INPUT, company)
        self._debug("Clicking login button")
        if self.is_visible(self.LOGIN_BUTTON_ID):
            self.click(self.LOGIN_BUTTON_ID)
        else:
            self.click(self.LOGIN_BUTTON)

    def wait_for_login_result(self) -> bool:
        def _logged_in(url: str) -> bool:
            url = (url or "").lower()
            return "nextgen" in url or "quick-access" in url or "home" in url

        try:
            self.page.wait_for_url(
                lambda url: _logged_in(url),
                timeout=self._timeout_ms,
            )
            self.page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
            self._debug(f"Post-login URL reached: {self.page.url}")
            return True
        except PlaywrightTimeoutError:
            self._debug(f"Login wait timed out. Current URL: {self.page.url}")
            return False
