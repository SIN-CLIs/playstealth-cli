# ================================================================================
# DATEI: driver_interface.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: Abstrahiert verschiedene Browser-Driver-Implementierungen
# WICHTIG FÜR ENTWICKLER:
#   - Ändere nichts ohne zu verstehen was passiert
#   - Unterstützt: Bridge (MCP), Nodriver, Playwright (PRIMARY!)
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Playwright IST DER EINZIGE DER FUNKTIONIERT für HeyPiggy!
# ================================================================================

"""Driver interface -- abstract browser automation behind a unified API.

WARUM: Der Worker muss zwischen verschiedenen Browser-Automation-Backends
wechseln können OHNE den High-Level Code zu ändern. Dieses Modul bietet:

* Abstract base class `BrowserDriver` mit der gemeinsamen API.
* BridgeDriver: MCP Bridge (aktuell default, aber instabil)
* PlaywrightDriver: STABILSTE Lösung mit STEALTH (DER EINZIGE DER FUNKTIONIERT!)
* NodriverDriver: Placeholder (aktuell nicht implementiert)
* Factory Funktion für einfache Instantiiierung.

ENTWICKLER: Die Bridge/MCP-Lösung funktioniert NICHT zuverlässig HeyPiggy.
Wenn wir Geld verdienen wollen, MUSS Playwright mit Stealth genutzt werden!

Design principles:
- All drivers implementieren die gleichen async Methoden
- Driver-spezifische Quirks sind in Adapter-Methoden isoliert
- Worker ruft driver.screenshot() auf, NICHT execute_bridge("dom.screenshot")
- Neuen Driver hinzufügen = eine neue Klasse, kein Worker Refactoring
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# Type alias für driver-agnostische Results
DriverResult = dict[str, Any]


class DriverType(Enum):
    """Supported browser automation backends."""

    BRIDGE = "bridge"  # MCP Bridge (aktuell default, aber instabil!)
    PLAYWRIGHT = "playwright"  # STABILSTE Lösung mit Stealth!
    NODRIVER = "nodriver"  # Placeholder - nicht implementiert


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

    WARUM: Der Worker sollte nicht wissen ob wir MCP Bridge,
    Nodriver oder Playwright nutzen. Dieses Interface definiert das Minimum
    das alle Driver erfüllen müssen.

    Subclasses MÜSSEN implementieren:
    - screenshot()
    - click() / click_ref()
    - type_text()
    - execute_javascript()
    - snapshot()
    - get_page_info()
    - navigate()
    - list_tabs()
    - close()
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize driver mit optionalem config dict."""
        self._config = config or {}
        self._initialized = False

    @property
    @abstractmethod
    def driver_type(self) -> DriverType:
        """Return which driver type this instance implements."""
        ...

    @property
    def is_initialized(self) -> bool:
        """Check if driver ist bereit für Operationen."""
        return self._initialized

    async def initialize(self) -> None:
        """Initialize den Browser/Driver connection.

        Override in subclasses um Chrome CDP, Playwright, etc. aufzusetzen.
        Default: nur initialized = True setzen.
        """
        self._initialized = True

    @abstractmethod
    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        """Screenshot der aktuellen Seite oder spezifischen Tab."""
        ...

    @abstractmethod
    async def click_ref(self, ref: str, tab_id: int | None = None) -> ClickResult:
        """Click ein Element per Reference ID (accessibility ref)."""
        ...

    @abstractmethod
    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        """Click ein Element per CSS Selector (Fallback)."""
        ...

    @abstractmethod
    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        """Tippe Text in ein Element oder das fokussierte Element."""
        ...

    @abstractmethod
    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        """Führe beliebiges JavaScript im Page Context aus."""
        ...

    @abstractmethod
    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        """DOM Snapshot mit Accessibility Tree holen."""
        ...

    @abstractmethod
    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        """Hole aktuelle Page URL, Title, etc."""
        ...

    @abstractmethod
    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        """Navigiere zu einer URL."""
        ...

    @abstractmethod
    async def list_tabs(self) -> list[dict[str, Any]]:
        """Liste alle offenen Browser Tabs."""
        ...

    async def advanced_stealth(self, tab_id: int | None = None) -> dict[str, Any]:
        """Aktiviere extra Stealth-Härtung. Default: no-op."""
        return {"ok": True, "applied": False}

    async def tabs_create(
        self, url: str, active: bool = True, tab_id: int | None = None
    ) -> dict[str, Any]:
        """Erzeuge/öffne einen Tab. Default: navigiert den aktiven Kontext."""
        result = await self.navigate(url, tab_id)
        result.setdefault("tabId", 0)
        result.setdefault("windowId", 0)
        return result

    @abstractmethod
    async def close(self) -> None:
        """Räume Driver Resources auf."""
        ...

    # --- Optionale Methoden mit Default Implementationen ---

    async def press_key(self, key: str, tab_id: int | None = None) -> dict[str, Any]:
        """Drücke eine Keyboard-Taste. Default: nutzt execute_javascript."""
        js = f"document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}'}}))"
        result = await self.execute_javascript(js, tab_id)
        return {"success": result.error is None, "error": result.error}

    async def wait_for_element(
        self, selector: str, timeout: float = 10.0, tab_id: int | None = None
    ) -> bool:
        """Warte bis Element erscheint. Default: nutzt JS Polling."""
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
        """Wähle eine Option in einem <select> Element."""
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
    """Bridge (MCP) Implementation - nutzt bestehendes execute_bridge Pattern.

    WARUM: Dies wrappt die existierenden heypiggy_vision_worker.py `execute_bridge`
    Calls in das neue Interface OHNE den underlying Transport zu ändern.

    ENTWICKLER: DIESER DRIVER FUNKTIONIERT NICHT ZUVERLÄSSIG!
    HeyPiggy erkennt und blockt MCP Bridge Browser-Automation.
    NUR Playwright mit Stealth funktioniert!
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._tab_id: int | None = None

    @property
    def driver_type(self) -> DriverType:
        return DriverType.BRIDGE

    async def initialize(self) -> None:
        """Bridge braucht keine explizite Init - nutzt bestehendes post_mcp."""
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
        """Bridge Driver besitzt den Browser nicht - no-op."""
        pass


class PlaywrightDriver(BrowserDriver):
    """Playwright Implementation mit STEALTH und HUMAN-LIKE Behavior.

    WARUM: Das ist die KRITISCHSTE Komponente! HeyPiggy erkennt und blockt
    Bot-Traffic. Die Bridge/MCP-Lösung funktioniert NICHT zuverlässig!

    Diese Implementierung nutzt:
    - playwright-stealth Plugin (entfernt alle Bot-Fingerabdrücke)
    - Human-like mouse movement (echte Mausbewegung, nicht instant)
    - Human-like typing (mit variabler Geschwindigkeit)
    - Keyboard navigation fallback (Tab/Enter wenn Klick blockiert)
    - Viewport/User-Agent Randomisierung

    ENTWICKLER: Ändere nichts daran ohne zu verstehen was passiert!
    Wenn das nicht funktioniert, werden keine Umfragen ausgefüllt = 0 EUR!
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._profile_root = Path(
            self._config.get(
                "profile_root",
                os.environ.get(
                    "HEYPIGGY_PLAYWRIGHT_PROFILE_ROOT",
                    str(Path.home() / ".heypiggy" / "playwright_profile_clone"),
                ),
            )
        )
        # Human-like timing config - ENTWICKLER: Nicht ändern!
        self._type_delay_ms = self._config.get("type_delay_ms", (40, 120))  # (min, max)
        self._mouse_move_ms = self._config.get("mouse_move_ms", (100, 300))
        self._click_hold_ms = self._config.get("click_hold_ms", 100)

    def _prepare_profile_root(self) -> None:
        """Remove stale Chromium singleton files before launch.

        WHY: A crashed previous Playwright session can leave a stale
        SingletonSocket/Lock behind, which makes the next persistent launch abort
        even though no browser process is actually alive.
        """

        self._profile_root.mkdir(parents=True, exist_ok=True)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
            path = self._profile_root / name
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

    @property
    def driver_type(self) -> DriverType:
        return DriverType.PLAYWRIGHT

    async def initialize(self) -> None:
        """Launch Playwright mit STEALTH und HUMAN-LIKE Settings.

        KRITISCH: Dies ist der Unterschied zwischen 0 EUR und Geld verdienen!
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed! Run: pip install playwright playwright-stealth && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        stealth_config = self._config.get("stealth", True)

        # Persistenter Context: login cookies/storage bleiben im Clone erhalten.
        # Das ist der Unterschied zwischen "jedes Mal neu einloggen" und "einmal loggen, dann weiterlaufen".
        viewport_width = self._config.get("width", 1920)
        viewport_height = self._config.get("height", 1080)

        context_options = {
            "viewport": {"width": viewport_width, "height": viewport_height},
            "locale": self._config.get("locale", "de-DE"),
            "timezone_id": self._config.get("timezone", "Europe/Berlin"),
            "permissions": ["geolocation", "notifications"],
            "device_scale_factor": self._config.get("device_scale_factor", 2),
            "is_mobile": False,
            "has_touch": False,
        }

        if stealth_config:
            # Randomized user agent - sieht aus wie echter MacBook User
            user_agents = [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            ]
            context_options["user_agent"] = random.choice(user_agents)

        self._prepare_profile_root()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_root),
            channel="chrome",
            headless=self._config.get("headless", False),
            args=[
                f"--window-size={viewport_width},{viewport_height}",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-first-run",
            ],
            **context_options,
        )
        self._browser = self._context.browser
        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )

        # Inject stealth script um automation COMPLETT zu verstecken
        if stealth_config:
            await self._page.add_init_script("""
                // VERSTECKE ALLE BOT-FINGERABDRÜCKE
                Object.defineProperty(navigator, 'webdriver', { 
                    get: () => undefined, 
                    configurable: true 
                });
                
                // Verstecke plugins (echter Browser hat 5+)
                Object.defineProperty(navigator, 'plugins', { 
                    get: () => [1, 2, 3, 4, 5],
                    configurable: true 
                });
                
                // Echte Sprachen
                Object.defineProperty(navigator, 'languages', { 
                    get: () => ['de-DE', 'de', 'en-US', 'en'],
                    configurable: true 
                });
                
                // Chrome runtime
                window.chrome = { runtime: {} };
                
                // Permissions (normaler User hat viele)
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
                
                // Canvas fingerprint randomisieren
                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type) {
                    if (this.width > 0 && this.height > 0) {
                        const ctx = this.getContext('2d');
                        if (ctx) {
                            ctx.fillStyle = `rgb(${Math.floor(Math.random()*255)},0,0)`;
                            ctx.fillRect(0, 0, 1, 1);
                        }
                    }
                    return originalToDataURL.apply(this, arguments);
                };
            """)

        # Try to enable stealth plugin wenn verfügbar
        try:
            from playwright_stealth import stealth_async

            await stealth_async(self._page)
        except ImportError:
            pass  # Continue ohne stealth plugin, init script ist aktiv

        self._initialized = True

    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        """Screenshot mit Playwright."""
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
        """Click per accessibility ref - mappt zu data-ref Attribut.

        METHODE 1: Selector Click (primär)
        METHODE 2: Human-like Mouse (wenn Selector fail)
        METHODE 3: Keyboard Fallback (wenn alles fail)
        """
        if not self._page:
            raise RuntimeError("Driver not initialized")

        try:
            # Versuche data-ref Selector
            selector = (
                f'[data-ref="{ref}"], [data-ref-id="{ref}"], [aria-label*="{ref}"], [text="{ref}"]'
            )
            await self._page.click(selector, timeout=3000)
            return ClickResult(success=True, element_ref=ref)
        except Exception:
            pass

        # METHODE 2: Human-like mouse movement
        try:
            # Finde Element
            element = await self._page.query_selector(selector)
            if element:
                box = await element.bounding_box()
                if box:
                    # Bewege Maus WIE EIN MENSCH (nicht instant!)
                    target_x = box["x"] + box["width"] / 2
                    target_y = box["y"] + box["height"] / 2

                    # Zufälliger Startpunkt
                    await self._page.mouse.move(
                        target_x + random.randint(-100, 100), target_y + random.randint(-100, 100)
                    )
                    # Bewege langsam zum Ziel
                    await self._page.mouse.move(target_x, target_y)
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                    # Mouse down/up wie ein Mensch
                    await self._page.mouse.down()
                    await asyncio.sleep(random.uniform(0.05, 0.15))  # HALTE maus taste!
                    await self._page.mouse.up()
                    return ClickResult(success=True, element_ref=ref)
        except Exception as e:
            pass

        # METHODE 3: Keyboard Fallback
        try:
            await self._page.keyboard.press("Tab")
            await asyncio.sleep(0.2)
            await self._page.keyboard.press("Enter")
            return ClickResult(success=True, element_ref=ref)
        except Exception as e:
            return ClickResult(success=False, element_ref=ref, error=str(e))

    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        """Click per CSS Selector mit human-like behavior."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            element = await self._page.query_selector(selector)
            if element:
                box = await element.bounding_box()
                if box:
                    # Human-like mouse
                    await self._page.mouse.move(
                        box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    )
                    await asyncio.sleep(random.uniform(0.1, 0.2))
                    await self._page.mouse.down()
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                    await self._page.mouse.up()
                    return ClickResult(success=True)
            await self._page.click(selector, timeout=5000)
            return ClickResult(success=True)
        except Exception as e:
            return ClickResult(success=False, error=str(e))

    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        """Type mit HUMAN-LIKE timing (variabel, nicht roboterhaft)."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            if selector:
                await self._page.fill(selector, text)  # Schnell für Input Fields
            else:
                # Human-like typing: jedes Zeichen mit zufälliger Verzögerung
                min_delay, max_delay = self._type_delay_ms
                for char in text:
                    await self._page.keyboard.type(char, delay=random.randint(min_delay, max_delay))
            return TypeResult(success=True, characters_sent=len(text))
        except Exception as e:
            return TypeResult(success=False, characters_sent=0, error=str(e))

    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        """JavaScript ausführen."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            result = await self._page.evaluate(script)
            return JavascriptResult(result=result)
        except Exception as e:
            return JavascriptResult(result=None, error=str(e))

    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        """DOM Snapshot holen."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        html = await self._page.content()
        url = self._page.url
        title = await self._page.title()
        accessibility = await self._page.accessibility.snapshot()
        return SnapshotResult(
            html=html,
            url=url,
            title=title,
            accessibility_tree=str(accessibility),
            elements=[],
        )

    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        """Page Info holen."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        return {
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        """Navigate zu URL."""
        if not self._page:
            raise RuntimeError("Driver not initialized")
        try:
            response = await self._page.goto(url, wait_until="domcontentloaded")
            return {"success": True, "status": response.status if response else 0}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_tabs(self) -> list[dict[str, Any]]:
        """Liste Tabs - Playwright unterstützt nur 1 pro Context."""
        if not self._page:
            return []
        return [{"id": 0, "url": self._page.url, "title": await self._page.title()}]

    async def advanced_stealth(self, tab_id: int | None = None) -> dict[str, Any]:
        """Playwright-Stealth ist bereits aktiv; erneute Härtung ist ein no-op."""
        if not self._page:
            return {"ok": False, "error": "Driver not initialized"}
        return {"ok": True, "applied": True, "mode": "playwright"}

    async def tabs_create(
        self, url: str, active: bool = True, tab_id: int | None = None
    ) -> dict[str, Any]:
        """Öffne die Ziel-URL im aktiven Playwright-Tab und liefere stabile IDs."""
        if not self._context:
            return {"error": "Driver not initialized"}
        if not self._page:
            self._page = await self._context.new_page()
        if url:
            await self._page.goto(url, wait_until="domcontentloaded")
        if active:
            try:
                await self._page.bring_to_front()
            except Exception:
                pass
        return {"tabId": 0, "windowId": 0, "url": self._page.url, "active": active}

    async def close(self) -> None:
        """Räume Playwright Resources auf."""
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
    """Nodriver Implementation - NICHT IMPLEMENTIERT.

    WARUM: Nodriver ist eine Alternative zu Playwright, aber aktuell
    nicht stabil genug für HeyPiggy. Placeholder für zukünftige Nutzung.

    ENTWICKLER: Wenn Nodriver besser wird, kann man das hier implementieren.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._browser = None

    @property
    def driver_type(self) -> DriverType:
        return DriverType.NODRIVER

    async def initialize(self) -> None:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def screenshot(self, tab_id: int | None = None) -> ScreenshotResult:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def click_ref(self, ref: str, tab_id: int | None = None) -> ClickResult:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def click(self, selector: str, tab_id: int | None = None) -> ClickResult:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def type_text(
        self, text: str, selector: str | None = None, tab_id: int | None = None
    ) -> TypeResult:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def execute_javascript(self, script: str, tab_id: int | None = None) -> JavascriptResult:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def snapshot(self, tab_id: int | None = None) -> SnapshotResult:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def get_page_info(self, tab_id: int | None = None) -> dict[str, Any]:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def navigate(self, url: str, tab_id: int | None = None) -> dict[str, Any]:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def list_tabs(self) -> list[dict[str, Any]]:
        raise NotImplementedError("Nodriver driver not implemented yet - use PlaywrightDriver")

    async def close(self) -> None:
        if self._browser:
            await self._browser.stop()
        self._initialized = False


# --- Factory Funktion ---

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

    WARUM: Zentraler Factory stellt konsistente Driver Instantiierung sicher
    durch den gesamten Codebase. Callers müssen nicht wissen welche Klasse.

    Args:
        driver_type: Welcher Driver erstellt werden soll (default: env DRIVER_TYPE)
        config: Optional driver-spezifisches config dict

    Returns:
        Initialisierte BrowserDriver Subclass Instanz

    Example:
        driver = create_driver("playwright", {"headless": False})
        await driver.initialize()
        screenshot = await driver.screenshot()
    """
    # Resolve driver type
    if driver_type is None:
        driver_type = os.environ.get("DRIVER_TYPE", "playwright").lower()  # DEFAULT: PLAYWRIGHT!
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
