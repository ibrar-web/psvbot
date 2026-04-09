import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimatedSummaryTab(BasePage):
    ESTIMATE_SUMMARY_TAB = "//li[@role='tab' and .//span[normalize-space()='Estimate Summary']]"
    CREATE_PROSPECT_BUTTON = (
        "//button[@name='create_account_button'"
        " and .//span[contains(normalize-space(),'Create Prospect')]]"
    )
    US685_E_ESTIMATE_BUTTON = (
        "//div[@name='print_btn_group']//button[@name='print_btn'"
        " and .//span[normalize-space()='US685 E-Estimate']]"
    )

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimatedSummaryTab] {message}")
            logger.info(message)

    def is_visible(self) -> bool:
        return super().is_visible(By.XPATH, self.ESTIMATE_SUMMARY_TAB)

    def switch_to_tab(self) -> None:
        self.wait_for_spinner_to_disappear()
        self.click(By.XPATH, self.ESTIMATE_SUMMARY_TAB)
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: bool(
                d.execute_script(
                    """
                    const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                    const target = tabs.find(t => (t.innerText || "").includes("Estimate Summary"));
                    return !!target && target.getAttribute("aria-selected") === "true";
                    """
                )
            )
        )
        self.wait_for_spinner_to_disappear()

    def click_us685_eestimate_and_download(
        self,
        customer_selection_status: Optional[Dict[str, Any]] = None,
    ) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="psv_invoices_"))
        customer_selection_status = customer_selection_status or {}

        if customer_selection_status.get("used_fallback_customer"):
            self._create_prospect()

        self._debug("Waiting for US685 E-Estimate button on Estimate Summary")
        existing_handles = list(self.driver.window_handles)
        current_handle = self.driver.current_window_handle

        self.click(By.XPATH, self.US685_E_ESTIMATE_BUTTON)
        self._debug("US685 E-Estimate clicked; waiting for generated document tab")

        new_handle = WebDriverWait(self.driver, max(self.timeout, 120)).until(
            lambda d: next(
                (handle for handle in d.window_handles if handle not in existing_handles),
                None,
            )
        )

        self.driver.switch_to.window(new_handle)
        try:
            download_url = self._wait_for_download_url()
            self._debug(f"Resolved invoice download URL: {download_url}")
            saved_path = self._download_invoice(download_url, temp_dir)
            self._debug(f"Invoice downloaded to: {saved_path}")
            return saved_path
        finally:
            try:
                self.driver.close()
            finally:
                self.driver.switch_to.window(current_handle)

    def _create_prospect(self) -> None:
        self._debug("Creating prospect before downloading estimate")
        self.wait_for_spinner_to_disappear()
        self.click(By.XPATH, self.CREATE_PROSPECT_BUTTON)
        self.wait_for_spinner_to_disappear()

    def _wait_for_download_url(self) -> str:
        def resolve_url(driver) -> str | None:
            url = (driver.current_url or "").strip()
            if url.startswith(("http://", "https://")):
                return url

            return driver.execute_script(
                """
                const candidates = [
                  document.querySelector("embed[type='application/pdf']")?.src,
                  document.querySelector("iframe")?.src,
                  document.querySelector("object")?.data,
                  ...performance.getEntriesByType("resource").map(entry => entry.name),
                ].filter(Boolean);
                return candidates.find(value => /^https?:/i.test(value)) || null;
                """
            )

        download_url = WebDriverWait(self.driver, max(self.timeout, 120)).until(resolve_url)
        if not download_url:
            raise TimeoutException("Unable to resolve generated invoice download URL")
        return download_url

    def _download_invoice(self, url: str, target_dir: Path) -> Path:
        request = Request(
            url,
            headers={
                "Cookie": self._cookie_header(),
                "User-Agent": self.driver.execute_script("return navigator.userAgent;"),
                "Referer": self.driver.current_url,
            },
        )

        with urlopen(request, timeout=max(self.timeout, 120)) as response:
            filename = self._build_filename(url, response.headers.get("Content-Disposition", ""))
            target_path = self._unique_path(target_dir / filename)
            with target_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        return target_path

    def _cookie_header(self) -> str:
        return "; ".join(
            f"{cookie['name']}={cookie['value']}"
            for cookie in self.driver.get_cookies()
            if cookie.get("name")
        )

    def _build_filename(self, url: str, content_disposition: str) -> str:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition or "", re.I)
        if match:
            filename = unquote(match.group(1).strip())
        else:
            filename = Path(urlparse(url).path).name or f"invoice_{int(time.time())}.pdf"

        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or f"invoice_{int(time.time())}.pdf"
        if "." not in filename:
            filename = f"{filename}.pdf"
        return filename

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        timestamp = int(time.time())
        return path.with_name(f"{stem}_{timestamp}{suffix}")
