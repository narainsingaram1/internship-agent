"""Playwright Browser Management with stealth mode and headed visualization."""
import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


class BrowserSession:
    """Manages an interactive Playwright browser session."""

    def __init__(self, headless: bool = False, slow_mo: int = 40):
        self.headless = headless
        self.slow_mo = slow_mo
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def start(self) -> Page:
        """Launch browser session with anti-detection flags."""
        logger.info(f"Starting browser session (headless={self.headless})...")
        self._playwright = sync_playwright().start()

        # Stealth launch arguments
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]

        self.browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=launch_args,
        )

        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Remove navigator.webdriver flag
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        self.page = self.context.new_page()
        return self.page

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Page:
        """Navigate to a URL and wait for page stabilization."""
        if not self.page:
            self.start()
        logger.info(f"Navigating to {url}...")
        self.page.goto(url, wait_until=wait_until, timeout=45000)
        self.page.wait_for_timeout(2000)  # Allow dynamic scripts to mount
        return self.page

    def stop(self):
        """Cleanly close the browser session."""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self._playwright:
            self._playwright.stop()
        logger.info("Browser session closed.")
