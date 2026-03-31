import logging

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class LoginPage(BasePage):
    USERNAME_INPUT = "//input[@name='userName' or contains(@id,'user')]"
    PASSWORD_INPUT = (
        "//input[@type='password' or @name='password' or contains(@id,'password')]"
    )
    COMPANY_INPUT = "//input[@name='companyName' or contains(@id,'company')]"
    LOGIN_BUTTON_ID = "loginBtn"
    LOGIN_BUTTON = (
        "//input[@id='loginBtn' and @type='button']"
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
        self.type(By.XPATH, self.USERNAME_INPUT, username)
        self.type(By.XPATH, self.PASSWORD_INPUT, password)
        if company:
            self._debug("Filling company field")
            self.type_if_visible(By.XPATH, self.COMPANY_INPUT, company)
        self._debug("Clicking login button")
        if self.is_visible(By.ID, self.LOGIN_BUTTON_ID):
            self.click(By.ID, self.LOGIN_BUTTON_ID)
        else:
            self.click(By.XPATH, self.LOGIN_BUTTON)

    def wait_for_login_result(self) -> bool:
        def _logged_in_and_page_loaded(driver) -> bool:
            url = (driver.current_url or "").lower()
            is_logged_in_url = "nextgen" in url or "quick-access" in url or "home" in url
            if not is_logged_in_url:
                self._debug(f"Current URL is not post-login yet: {url}")
                return False

            try:
                is_ready = driver.execute_script("return document.readyState") == "complete"
            except WebDriverException:
                # Browser may still be navigating; retry until timeout.
                return False

            self._debug(f"Post-login URL reached. URL: {url}, readyState complete: {is_ready}")
            return is_ready

        return bool(WebDriverWait(self.driver, self.timeout).until(_logged_in_and_page_loaded))
