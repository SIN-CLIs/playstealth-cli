# ================================================================================
# DATEI: driver_interface.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: Abstrahiert verschiedene Browser-Driver-Implementierungen
# WICHTIG FÜR ENTWICKLER:
#   - Ändere nichts ohne zu verstehen was passiert
#   - Unterstützt: Bridge (MCP), Nodriver, Playwright
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
# ================================================================================

"""Driver interface -- abstract browser automation behind a unified API.

WHY: The worker needs to switch between different browser automation backends
without changing the high-level code. This module provides:

* Abstract base class `BrowserDriver` defining the common interface.
* Concrete implementations for Bridge (MCP), Nodriver, and Playwright.
* Factory function to instantiate the correct driver based on config.
* Unified result types so the worker sees consistent output.

Design principles:
- All drivers implement the same async methods (screenshot, click, type, etc.)
- Driver-specific quirks are isolated in adapter methods
- The worker calls `driver.screenshot()` not `execute_bridge("dom.screenshot")`
- Adding a new driver = one new class, no worker refactoring
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

# Type alias for driver-agnostic results
DriverResult = dict[str, Any]


class DriverType(Enum):
    """Supported browser automation backends."""

    BRIDGE = "bridge"  # MCP Bridge (current default)
    NODRIVER = "nodriver"  # Direct Chrome via nodriver
    PLAYWRIGHT = "playwright"  # Playwright with stealth


@dataclass(frozen=True)
class ScreenshotResult:
    """Unified screenshot result across all drivers."""

    data_url: str  # Base64 data URL
    width: int
    height: int
    path: Path | None = None  # Optional saved file path


@dataclass(frozen=True)
class ClickResult:
    """Unified click result across all drivers."""

    success: bool
    element_ref: str | None = None  # For click_ref support
    error: str | None = None


@dataclass(frozen=True)
class TypeResult:
    """Unified type result across all drivers."""

    success: bool
    characters_sent: int = 0
    error: str | None = None


@dataclass(frozen=True)
class JavascriptResult:
    """Unified JavaScript execution result."""

    result: Any
    error: str | None = None


@dataclass(frozen=True)
class SnapshotResult:
    """Unified DOM snapshot result."""

    html: str
    url: str
    title: str
    accessibility_tree: str  # Unified across drivers
    elements: list[dict[str, Any]]  # For click_ref


class BrowserDriver(ABC):
    """Abstract base class for all browser drivers.

    WHY: The worker should not care whether we're using MCP Bridge,
    Nodriver, or Playwright. This interface defines the minimum
    contract all drivers must satisfy.

    Subclasses MUST implement:
    - screenshot()
    - click() / click_ref()
    - type_text()
    - execute_javascript()
    - snapshot()
    - get_page_info()
    - navigate()
    - list_tabs()
    - close()

    Subclasses SHOULD implement:
    - wait_for_element()
    - press_key()
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize driver with optional config dict."""
        self._config = config or {}
        self._initialized = False

    @property
    @abstractmethod
    def driver_type(self) -> DriverType:
        """Return which driver type this instance implements."""
        ...

    @property
    def is_initialized(self) -> bool:
        """Check if driver is ready for operations."""
        return self._initialized

    async def initialize(self) -> None:
        """Initialize the browser/driver connection.

        Override in subclasses to set up Chrome CDP, Playwright, etc.
        Default implementation just marks initialized = True.
        """
        self._initialized = True

    @abstractmethod
    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        """Capture a screenshot of the current page or specific tab."""
        ...

    @abstractmethod
    async def click_ref(self, ref: str, tab_id: int | None = None) -> ClickResult:
        """Click an element by reference ID (accessibility ref)."""
        ...

    @abstractmethod
    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        """Click an element by CSS selector (fallback method)."""
        ...

    @abstractmethod
    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        """Type text into an element or the focused element."""
        ...

    @abstractmethod
    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        """Execute arbitrary JavaScript in the page context."""
        ...

    @abstractmethod
    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        """Get DOM snapshot with accessibility tree."""
        ...

    @abstractmethod
    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        """Get current page URL, title, etc."""
        ...

    @abstractmethod
    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        """Navigate to a URL."""
        ...

    @abstractmethod
    async def list_tabs(self) -> list[dict[str, Any]]:
        """List all open browser tabs."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up driver resources."""
        ...

    # --- Optional methods with default implementations ---

    async def press_key(self, key: str, tab_id: int | None = None) -> dict[str, Any]:
        """Press a keyboard key. Default: uses execute_javascript."""
        js = f"document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}'}}))"
        result = await self.execute_javascript(js, tab_id)
        return {"success": result.error is None, "error": result.error}

    async def wait_for_element(
        self, selector: str, timeout: float = 10.0, tab_id: int | None = None
    ) -> bool:
        """Wait for element to appear. Default: uses JS polling."""
        js = f"""
        new Promise((resolve) => {{
            const el = document.querySelector('{selector}');
            if (el) {{ resolve(true); return; }}
            const observer = new MutationObserver(() => {{
                if (document.querySelector('{selector}')) {{
                    observer.disconnect(); resolve(true);
                }}
            }});
            observer.observe(document.body, {{ childList: true, subtree: true }});
            setTimeout(() => {{ observer.disconnect(); resolve(false); }}, {timeout * 1000});
        }})
        """
        result = await self.execute_javascript(js, tab_id)
        return result.result is True

    async def select_option(
        self, selector: str, value: str, tab_id: int | None = None
    ) -> dict[str, Any]:
        """Select an option in a <select> element."""
        js = f"""
        (function() {{
            const sel = document.querySelector('{selector}');
            if (!sel) return {{ error: 'no selector' }};
            sel.value = '{value}';
            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return {{ success: true }};
        }})()
        """
        result = await self.execute_javascript(js, tab_id)
        return {"success": result.result.get("success", False), "error": result.error}


class BridgeDriver(BrowserDriver):
    """Bridge (MCP) implementation using the existing execute_bridge pattern.

    WHY: This wraps the existing heypiggy_vision_worker.py `execute_bridge` calls
    in the new interface without changing the underlying transport.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._tab_id: int | None = None

    @property
    def driver_type(self) -> DriverType:
        return DriverType.BRIDGE

    async def initialize(self) -> None:
        """Bridge doesn't need explicit init - uses existing post_mcp."""
        self._initialized = True

    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        result = await execute_bridge("observe", {"tabId": tid} if tid else {})
        screenshot = result.get("screenshot", {})
        data = screenshot.get("dataUrl", "") if isinstance(screenshot, dict) else ""
        return ScreenshotResult(
            data_url=data,
            width=screenshot.get("width", 0),
            height=screenshot.get("height", 0),
        )

    async def click_ref(self, ref: str, tab_id: int | None = None) -> ClickResult:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"ref": ref}
        if tid:
            params["tabId"] = tid
        result = await execute_bridge("click_ref", params)
        return ClickResult(
            success=result.get("success", False),
            element_ref=ref,
            error=result.get("error"),
        )

    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"selector": selector}
        if tid:
            params["tabId"] = tid
        result = await execute_bridge("dom.click", params)
        return ClickResult(
            success=result.get("success", False),
            error=result.get("error"),
        )

    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"text": text}
        if selector:
            params["selector"] = selector
        if tid:
            params["tabId"] = tid
        result = await execute_bridge("dom.type", params)
        return TypeResult(
            success=result.get("success", False),
            characters_sent=len(text),
            error=result.get("error"),
        )

    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"script": script}
        if tid:
            params["tabId"] = tid
        result = await execute_bridge("execute_javascript", params)
        return JavascriptResult(result=result.get("result"), error=result.get("error"))

    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"includeScreenshot": False}
        if tid:
            params["tabId"] = tid
        result = await execute_bridge("snapshot", params)
        return SnapshotResult(
            html=result.get("html", ""),
            url=result.get("url", ""),
            title=result.get("title", ""),
            accessibility_tree=result.get("accessibility_tree", ""),
            elements=result.get("elements", []),
        )

    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"tabId": tid} if tid else {}
        return await execute_bridge("get_page_info", params)

    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        from heypiggy_vision_worker import execute_bridge

        tid = tab_id or self._tab_id
        params = {"url": url}
        if tid:
            params["tabId"] = tid
        return await execute_bridge("goto", params)

    async def list_tabs(self) -> list[dict[str, Any]]:
        from heypiggy_vision_worker import execute_bridge

        result = await execute_bridge("tabs_list", {})
        return result.get("tabs", [])

    async def close(self) -> None:
        """Bridge driver doesn't own the browser - no-op."""
        pass


class PlaywrightDriver(BrowserDriver):
    """Playwright implementation with stealth settings.

    WHY: Playwright provides more stable browser automation than MCP Bridge,
    especially for anti-bot detection. This driver wraps Playwright with
    stealth settings to appear as a real user.

    Features:
    - Launches its own Chromium instance (not shared with Bridge)
    - Applies stealth patches (randomized viewport, user-agent, etc.)
    - No extension dependency
    - Direct CDP access for maximum control
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def driver_type(self) -> DriverType:
        return DriverType.PLAYWRIGHT

    async def initialize(self) -> None:
        """Launch Playwright with stealth settings."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        stealth_config = self._config.get("stealth", True)

        # Launch browser with stealth settings
        launch_options = {
            "headless": self._config.get("headless", True),
        }
        if stealth_config:
            # Stealth mode: hide automation flags
            launch_options["args"] = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]

        self._browser = await self._playwright.chromium.launch(**launch_options)

        # Create context with randomized settings
        context_options = {
            "viewport": {
                "width": self._config.get("width", 1920),
                "height": self._config.get("height", 1080),
            },
            "locale": self._config.get("locale", "de-DE"),
            "timezone_id": self._config.get("timezone", "Europe/Berlin"),
        }
        if stealth_config:
            # Randomized user agent
            context_options["user_agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )

        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()

        # Inject stealth script to hide automation
        if stealth_config:
            await self._page.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
                """
            )

        self._initialized = True

    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        img = await self._page.screenshot(format="jpeg", quality=85)
        import base64

        data_url = f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"
        viewport = self._page.viewport_size or {}
        return ScreenshotResult(
            data_url=data_url,
            width=viewport.get("width", 0),
            height=viewport.get("height", 0),
        )

    async def click_ref(self, ref: str, tab_id: int | None = None) -> ClickResult:
        """Click by accessibility ref - maps to data-ref attribute."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            # Try data-ref first, then aria-label, then fallback
            selector = (
                f'[data-ref="{ref}"], [data-ref-id="{ref}"], [aria-label*="{ref}"], [text="{ref}"]'
            )
            await self._page.click(selector, timeout=5000)
            return ClickResult(success=True, element_ref=ref)
        except Exception as e:
            return ClickResult(success=False, element_ref=ref, error=str(e))

    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            await self._page.click(selector, timeout=5000)
            return ClickResult(success=True)
        except Exception as e:
            return ClickResult(success=False, error=str(e))

    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            if selector:
                await self._page.fill(selector, text)
            else:
                await self._page.keyboard.type(text, delay=50)  # Human-like typing
            return TypeResult(success=True, characters_sent=len(text))
        except Exception as e:
            return TypeResult(success=False, characters_sent=0, error=str(e))

    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            result = await self._page.evaluate(script)
            return JavascriptResult(result=result)
        except Exception as e:
            return JavascriptResult(result=None, error=str(e))

    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        html = await self._page.content()
        url = self._page.url
        title = await self._page.title()
        # Generate simple accessibility tree
        accessibility = await self._page.accessibility.snapshot()
        return SnapshotResult(
            html=html,
            url=url,
            title=title,
            accessibility_tree=str(accessibility),
            elements=[],  # Playwright doesn't expose elements the same way
        )

    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        return {
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            response = await self._page.goto(url, wait_until="domcontentloaded")
            return {"success": True, "status": response.status if response else 0}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_tabs(self) -> list[dict[str, Any]]:
        # Playwright context manages one page per context
        # Multi-tab requires multiple contexts - not implemented yet
        if not self._page:
            return []
        return [{"id": 0, "url": self._page.url, "title": await self._page.title()}]

    async def close(self) -> None:
        """Clean up Playwright resources."""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._initialized = False


class NodriverDriver(BrowserDriver):
    """Nodriver implementation - direct Chrome without CDP bridge.

    WHY: Nodriver provides direct Chrome automation similar to Playwright
    but with a different API. This is a placeholder - implementation depends
    on nodriver library specifics.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._browser = None

    @property
    def driver_type(self) -> DriverType:
        return DriverType.NODRIVER

    async def initialize(self) -> None:
        try:
            import nodriver
        except ImportError:
            raise RuntimeError("Nodriver not installed. Run: pip install nodriver")

        # Nodriver starts Chrome automatically
        self._browser = await nodriver.start()
        self._initialized = True

    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def click_ref(self, ref: str, tab_id: int | None = None) -> ClickResult:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def list_tabs(self) -> list[dict[str, Any]]:
        raise NotImplementedError("Nodriver driver not fully implemented yet")

    async def close(self) -> None:
        if self._browser:
            await self._browser.stop()
        self._initialized = False


# --- Factory function ---

_DRIVER_CLASSES: dict[DriverType, type[BrowserDriver]] = {
    DriverType.BRIDGE: BridgeDriver,
    DriverType.PLAYWRIGHT: PlaywrightDriver,
    DriverType.NODRIVER: NodriverDriver,
}


def create_driver(
    driver_type: DriverType | str | None = None,
    config: dict[str, Any] | None = None,
) -> BrowserDriver:
    """Create a browser driver instance based on type.

    WHY: Central factory ensures consistent driver instantiation throughout
    the codebase. Callers don't need to know which class to import.

    Args:
        driver_type: Which driver to create (defaults to BRIDGE or env DRIVER_TYPE)
        config: Optional driver-specific config dict

    Returns:
        Initialized BrowserDriver subclass instance

    Example:
        driver = create_driver("playwright", {"headless": False})
        await driver.initialize()
        screenshot = await driver.screenshot()
    """
    # Resolve driver type
    if driver_type is None:
        driver_type = os.environ.get("DRIVER_TYPE", "bridge").lower()
    if isinstance(driver_type, str):
        driver_type = DriverType(driver_type)

    driver_class = _DRIVER_CLASSES.get(driver_type)
    if driver_class is None:
        raise ValueError(
            f"Unknown driver type: {driver_type}. Available: {[d.value for d in DriverType]}"
        )

    return driver_class(config)


__all__ = [
    "BrowserDriver",
    "BridgeDriver",
    "PlaywrightDriver",
    "NodriverDriver",
    "DriverType",
    "ScreenshotResult",
    "ClickResult",
    "TypeResult",
    "JavascriptResult",
    "SnapshotResult",
    "create_driver",
]
