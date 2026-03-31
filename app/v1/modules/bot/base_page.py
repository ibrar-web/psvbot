import time
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
)

from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS


class BasePage:

    def __init__(
        self, driver: WebDriver, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.driver = driver
        self.timeout = timeout

    def wait_for_visible(self, by: By, locator: str) -> WebElement:
        return WebDriverWait(self.driver, self.timeout).until(
            EC.visibility_of_element_located((by, locator))
        )

    def wait_for_clickable(self, by: By, locator: str) -> WebElement:
        return WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((by, locator))
        )

    def wait_for_invisible(self, by: By, locator: str) -> bool:
        return WebDriverWait(self.driver, self.timeout).until(
            EC.invisibility_of_element_located((by, locator))
        )

    def find(self, by: By, locator: str) -> WebElement:
        return self.wait_for_visible(by, locator)

    def click(self, by: By, locator: str) -> None:
        self.wait_for_spinner_to_disappear()
        try:
            element = self.wait_for_clickable(by, locator)
            element.click()
        except ElementClickInterceptedException:
            self.wait_for_spinner_to_disappear()
            element = self.wait_for_visible(by, locator)
            self.driver.execute_script("arguments[0].click();", element)
        except TimeoutException:
            # Some PrintSmith controls are <input type="button"> and may not be marked clickable.
            self.wait_for_spinner_to_disappear()
            element = self.wait_for_visible(by, locator)
            self.driver.execute_script("arguments[0].click();", element)

    def type(self, by: By, locator: str, value: str, clear_first: bool = True) -> None:
        self.wait_for_spinner_to_disappear()
        element = self.wait_for_visible(by, locator)
        if clear_first:
            element.clear()
        element.send_keys(value)
        self.wait_for_spinner_to_disappear()

    def is_visible(self, by: By, locator: str) -> bool:
        try:
            elements = self.driver.find_elements(by, locator)
            return any(element.is_displayed() for element in elements)
        except Exception:
            return False

    def type_if_visible(
        self, by: By, locator: str, value: str, clear_first: bool = True
    ) -> bool:
        if not self.is_visible(by, locator):
            return False
        self.type(by, locator, value, clear_first=clear_first)
        return True

    def wait_for_spinner_to_disappear(self):
        time.sleep(0.2)
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: d.execute_script(
                """
                const overlay = document.querySelector('.spinner-overlay');
                const progress = document.querySelector('.ng-progress');

                // overlay is hidden
                const overlayHidden = !overlay || window.getComputedStyle(overlay).display === 'none';

                // progress bar is inactive (active class removed)
                const progressInactive = !progress || !progress.classList.contains('active');

                return overlayHidden && progressInactive;
                """
            )
        )
