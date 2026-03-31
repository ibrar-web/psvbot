import logging
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from app.v1.modules.bot.config import HEADLESS

logger = logging.getLogger(__name__)

MACOS_CHROME_PATHS = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium",
)
LINUX_CHROME_PATHS = (
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)
CHROMEDRIVER_PATHS = (
    Path("/usr/bin/chromedriver"),
    Path("/usr/local/bin/chromedriver"),
)


def _resolve_chrome_binary() -> str | None:
    configured_path = os.getenv("PRINTSMITH_CHROME_BINARY", "").strip()
    if configured_path and Path(configured_path).exists():
        return configured_path

    for path in MACOS_CHROME_PATHS:
        if path.exists():
            return str(path)

    for path in LINUX_CHROME_PATHS:
        if path.exists():
            return str(path)

    return None


def _resolve_chromedriver_binary() -> str | None:
    configured_path = os.getenv("PRINTSMITH_CHROMEDRIVER_PATH", "").strip()
    if configured_path and Path(configured_path).exists():
        return configured_path

    for path in CHROMEDRIVER_PATHS:
        if path.exists():
            return str(path)

    return None


def create_driver() -> webdriver.Chrome:
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--no-zygote")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--disable-dev-tools")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--remote-debugging-port=9222")

    chrome_binary = _resolve_chrome_binary()
    chromedriver_binary = _resolve_chromedriver_binary()
    if chrome_binary:
        options.binary_location = chrome_binary

    is_cloud_run = bool(os.getenv("K_SERVICE"))

    if not chrome_binary:
        raise RuntimeError(
            "Chrome binary not found. Install chromium/google-chrome in container "
            "or set PRINTSMITH_CHROME_BINARY."
        )
    if is_cloud_run and not chromedriver_binary:
        raise RuntimeError(
            "Chromedriver binary not found. Install chromedriver in container "
            "or set PRINTSMITH_CHROMEDRIVER_PATH."
        )

    logger.info(
        "Creating Chrome WebDriver (chrome=%s, chromedriver=%s)",
        chrome_binary,
        chromedriver_binary,
    )
    service = (
        Service(executable_path=chromedriver_binary)
        if chromedriver_binary
        else Service()
    )
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(0)
    return driver
