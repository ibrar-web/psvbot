import logging

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from app.v1.modules.bot.config import HEADLESS

logger = logging.getLogger(__name__)


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
            "--window-size=1728,1117",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1728, "height": 1117},
        accept_downloads=True,
    )
    page = context.new_page()
    return browser, context, page
