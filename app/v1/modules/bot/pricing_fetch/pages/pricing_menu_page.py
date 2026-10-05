import logging

from app.v1.modules.bot.base_page import BasePage

logger = logging.getLogger(__name__)


class PricingMenuPage(BasePage):
    PRICING_MENU = "#pricing > a"
    # Option <li> ids are duplicated in PSV, so options are matched by text.
    MENU_OPTION = (
        "xpath=//li[@id='pricing']"
        "//ul[contains(@class,'dot-dropdown-options-items')]"
        "//a[normalize-space()='{}']"
    )
    STOCK_DEFINITION = "Stock Definition"

    def open_stock_definition(self) -> None:
        self._open_option(self.STOCK_DEFINITION)

    def _open_option(self, option_text: str) -> None:
        logger.info("Opening Pricing > %s", option_text)
        self.click(self.PRICING_MENU)
        self.click(self.MENU_OPTION.format(option_text))
        self.wait_for_spinner_to_disappear()
        logger.info("Pricing > %s opened. URL: %s", option_text, self.page.url)
