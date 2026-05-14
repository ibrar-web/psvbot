import logging

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from app.v1.modules.bot.config import DEFAULT_TIMEOUT_SECONDS, HEADLESS

logger = logging.getLogger(__name__)

MACBOOK_PRO_14_VIEWPORT = {"width": 1500, "height": 982}


def create_browser_page(playwright: Playwright) -> tuple[Browser, BrowserContext, Page]:
    logger.info("Launching Playwright Chromium (headless=%s)", HEADLESS)
    browser = playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            "--no-sandbox",
            "--no-zygote",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-networking",
            f"--window-size={MACBOOK_PRO_14_VIEWPORT['width']},{MACBOOK_PRO_14_VIEWPORT['height']}",
        ],
    )
    context = browser.new_context(
        viewport=MACBOOK_PRO_14_VIEWPORT,
        accept_downloads=True,
    )
    page = context.new_page()
    timeout_ms = DEFAULT_TIMEOUT_SECONDS * 1000
    page.set_default_timeout(timeout_ms)
    page.set_default_navigation_timeout(timeout_ms)
    logger.info(
        "Page timeouts set: default=%dms navigation=%dms", timeout_ms, timeout_ms
    )
    return browser, context, page
