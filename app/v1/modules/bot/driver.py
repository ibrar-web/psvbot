import logging

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from app.v1.modules.bot.config import HEADLESS

logger = logging.getLogger(__name__)

MACBOOK_PRO_14_VIEWPORT = {"width": 1505, "height": 982}


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
    return browser, context, page
