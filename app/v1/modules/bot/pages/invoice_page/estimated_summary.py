import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.v1.modules.bot.base_page import BasePage
from app.v1.modules.bot.config import DEBUG

logger = logging.getLogger(__name__)


class EstimatedSummaryTab(BasePage):
    ESTIMATE_SUMMARY_TAB = "xpath=//li[@role='tab' and .//span[normalize-space()='Estimate Summary']]"
    CREATE_PROSPECT_BUTTON = (
        "xpath=//button[@name='create_account_button'"
        " and .//span[contains(normalize-space(),'Create Prospect')]]"
    )
    CREATE_PROSPECT_LINK = "xpath=//a[@name='create_account_button' and .//span[normalize-space()='Create Prospect']]"
    THREE_DOTS_BUTTON = "xpath=//div[contains(@class,'dot-more-options-icon')]"
    US685_E_ESTIMATE_BUTTON = (
        "xpath=//div[@name='print_btn_group']//button[@name='print_btn'"
        " and .//span[normalize-space()='US685 E-Estimate']]"
    )

    def _debug(self, message: str) -> None:
        if DEBUG:
            print(f"[PrintSmith][EstimatedSummaryTab] {message}")
        logger.info(message)

    def is_visible(self) -> bool:
        return super().is_visible(self.ESTIMATE_SUMMARY_TAB)

    def switch_to_tab(self) -> None:
        self.wait_for_spinner_to_disappear()
        self.click(self.ESTIMATE_SUMMARY_TAB)
        self.page.wait_for_function(
            """() => {
                const tabs = Array.from(document.querySelectorAll("li[role='tab']"));
                const target = tabs.find(t => (t.innerText || "").includes("Estimate Summary"));
                return !!target && target.getAttribute("aria-selected") === "true";
            }""",
            timeout=self._timeout_ms,
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

        # Use Playwright's built-in popup/new-page handling
        with self.page.context.expect_page(timeout=max(self._timeout_ms, 120_000)) as new_page_info:
            self.click(self.US685_E_ESTIMATE_BUTTON)
            self._debug("US685 E-Estimate clicked; waiting for generated document tab")

        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=max(self._timeout_ms, 120_000))

        try:
            download_url = self._wait_for_download_url(new_page)
            self._debug(f"Resolved invoice download URL: {download_url}")
            cookies = self.page.context.cookies()
            saved_path = self._download_invoice(download_url, temp_dir, cookies, new_page)
            self._debug(f"Invoice downloaded to: {saved_path}")
            return saved_path
        finally:
            try:
                new_page.close()
            except Exception:
                pass

    def _create_prospect(self) -> None:
        self._debug("Creating prospect before downloading estimate")
        self.wait_for_spinner_to_disappear()

        # Check if the Create Prospect button is directly visible
        if super().is_visible(self.CREATE_PROSPECT_BUTTON):
            self._debug("Create Prospect button found directly; clicking it")
            self.click(self.CREATE_PROSPECT_BUTTON)
            self.wait_for_spinner_to_disappear()
            return

        # Otherwise open the three-dots menu and click the link inside it
        self._debug("Create Prospect button not found directly; opening three-dots menu")
        self.wait_for_visible(self.THREE_DOTS_BUTTON)
        self.click(self.THREE_DOTS_BUTTON)
        self.wait_for_visible(self.CREATE_PROSPECT_LINK)
        self.click(self.CREATE_PROSPECT_LINK)
        self.wait_for_spinner_to_disappear()

    def _wait_for_download_url(self, new_page) -> str:
        def resolve_url():
            url = (new_page.url or "").strip()
            if url.startswith(("http://", "https://")):
                return url
            return new_page.evaluate(
                """() => {
                    const candidates = [
                      document.querySelector("embed[type='application/pdf']")?.src,
                      document.querySelector("iframe")?.src,
                      document.querySelector("object")?.data,
                      ...performance.getEntriesByType("resource").map(entry => entry.name),
                    ].filter(Boolean);
                    return candidates.find(value => /^https?:/i.test(value)) || null;
                }"""
            )

        deadline = time.monotonic() + max(self.timeout, 120)
        while time.monotonic() < deadline:
            try:
                url = resolve_url()
                if url:
                    return url
            except Exception:
                pass
            new_page.wait_for_timeout(500)

        raise PlaywrightTimeoutError("Unable to resolve generated invoice download URL")

    def _download_invoice(self, url: str, target_dir: Path, cookies: list, new_page) -> Path:
        cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies if c.get("name")
        )
        user_agent = new_page.evaluate("() => navigator.userAgent")

        request = Request(
            url,
            headers={
                "Cookie": cookie_header,
                "User-Agent": user_agent,
                "Referer": new_page.url,
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
