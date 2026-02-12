"""Browser lifecycle management using Patchright with persistent context."""

import logging
from pathlib import Path
from typing import Optional, Dict, Any
from patchright.async_api import async_playwright, BrowserContext, Page, Playwright

from .exceptions import NetworkError

logger = logging.getLogger(__name__)


class BrowserManager:
    """Async context manager for Patchright browser with persistent profile."""

    def __init__(
        self,
        user_data_dir: str | Path,
        headless: bool = True,
        slow_mo: int = 0,
        viewport: Optional[Dict[str, int]] = None,
        user_agent: Optional[str] = None,
        **launch_options: Any
    ):
        """
        Initialize browser manager with persistent context.

        Args:
            user_data_dir: Path to Chromium user data directory (persistent profile)
            headless: Run browser in headless mode
            slow_mo: Slow down operations by specified milliseconds
            viewport: Browser viewport size (default: 1280x720)
            user_agent: Custom user agent string
            **launch_options: Additional Patchright launch options
        """
        self.user_data_dir = str(Path(user_data_dir).expanduser())
        self.headless = headless
        self.slow_mo = slow_mo
        self.viewport = viewport or {"width": 1280, "height": 720}
        self.user_agent = user_agent
        self.launch_options = launch_options

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._is_authenticated = False

    async def __aenter__(self) -> "BrowserManager":
        """Start browser and create context."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close browser and cleanup."""
        await self.close()

    async def start(self) -> None:
        """Start Patchright and launch persistent browser context."""
        try:
            self._playwright = await async_playwright().start()

            # Ensure user data dir exists
            Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)

            # Build context options
            context_options: Dict[str, Any] = {
                "headless": self.headless,
                "slow_mo": self.slow_mo,
                "viewport": self.viewport,
                **self.launch_options,
            }

            if self.user_agent:
                context_options["user_agent"] = self.user_agent

            # Launch persistent context (combines browser + context)
            self._context = await self._playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                **context_options,
            )

            logger.info(
                f"Persistent browser launched (headless={self.headless}, "
                f"user_data_dir={self.user_data_dir})"
            )

            # Use existing page or create one
            if self._context.pages:
                self._page = self._context.pages[0]
            else:
                self._page = await self._context.new_page()

            logger.info("Browser context and page ready")

        except Exception as e:
            await self.close()
            raise NetworkError(f"Failed to start browser: {e}")

    async def close(self) -> None:
        """Close persistent context and cleanup resources."""
        try:
            if self._context:
                await self._context.close()
                self._context = None
                self._page = None

            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

            logger.info("Browser closed")

        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    async def new_page(self) -> Page:
        """
        Create a new page in the current context.

        Returns:
            New Patchright page
        """
        if not self._context:
            raise RuntimeError("Browser context not initialized. Call start() first.")

        page = await self._context.new_page()
        return page

    @property
    def page(self) -> Page:
        """
        Get the main page.

        Returns:
            Main Patchright page
        """
        if not self._page:
            raise RuntimeError("Browser not started. Use async context manager or call start().")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """
        Get the browser context.

        Returns:
            Patchright browser context
        """
        if not self._context:
            raise RuntimeError("Browser context not initialized.")
        return self._context

    async def set_cookie(self, name: str, value: str, domain: str = ".linkedin.com") -> None:
        """
        Set a single cookie.

        Args:
            name: Cookie name
            value: Cookie value
            domain: Cookie domain
        """
        if not self._context:
            raise RuntimeError("No browser context")

        await self._context.add_cookies([{
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/"
        }])

        logger.debug(f"Cookie set: {name}")

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return self._is_authenticated

    @is_authenticated.setter
    def is_authenticated(self, value: bool) -> None:
        """Set authentication status."""
        self._is_authenticated = value
