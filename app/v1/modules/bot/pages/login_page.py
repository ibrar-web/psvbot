import logging
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG, DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class InvalidLoginCredentialsError(Exception):
    pass


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
    INVALID_LOGIN_TEXT = "Invalid Login ID or Password: Please try again."
    INVALID_LOGIN_FRAGMENT = "invalid login id or password"
    INVALID_LOGIN_USER_MESSAGE = (
        "PrintSmith login failed because the stored PSV credentials are invalid. "
        "Update the store's PSV credentials with valid values before trying again."
    )

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][LoginPage] {message}")
            logger.info(message)

    def __init__(self, page, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        super().__init__(page, timeout)
        self._last_dialog_message: str | None = None

    def _capture_login_dialog(self) -> None:
        self._last_dialog_message = None

        def _handle_dialog(dialog) -> None:
            self._last_dialog_message = (dialog.message or "").strip()
            self._debug(f"Login dialog detected: {self._last_dialog_message}")
            dialog.accept()

        self.page.once("dialog", _handle_dialog)

    def _read_invalid_login_message(self) -> str | None:
        if self._last_dialog_message and self.INVALID_LOGIN_FRAGMENT in self._last_dialog_message.lower():
            return self._last_dialog_message

        try:
            body_text = self.page.evaluate(
                """() => {
                    const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    const match = text.match(/Invalid Login ID or Password:\\s*Please try again\\.?/i);
                    return match ? match[0].trim() : null;
                }"""
            )
        except PlaywrightError as exc:
            if "Execution context was destroyed" in str(exc):
                return None
            raise
        if body_text and self.INVALID_LOGIN_FRAGMENT in body_text.lower():
            return body_text
        return None

    def login(self, username: str, password: str, company: str) -> None:
        self._capture_login_dialog()
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

    def wait_for_login_result(self) -> None:
        def _logged_in(url: str) -> bool:
            url = (url or "").lower()
            return "nextgen" in url or "quick-access" in url or "home" in url

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            invalid_login_message = self._read_invalid_login_message()
            if invalid_login_message:
                raise InvalidLoginCredentialsError(self.INVALID_LOGIN_USER_MESSAGE)

            if _logged_in(self.page.url):
                self.page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
                self._debug(f"Post-login URL reached: {self.page.url}")
                return

            self.page.wait_for_timeout(250)

        self._debug(f"Login wait timed out. Current URL: {self.page.url}")
        raise PlaywrightTimeoutError(f"Login wait timed out. Current URL: {self.page.url}")
