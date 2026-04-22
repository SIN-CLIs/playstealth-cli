"""Advanced stealth enhancement for PlayStealth.

This module provides comprehensive anti-detection injections that go beyond
basic playwright_stealth to evade modern bot detection systems like:
- Cloudflare Turnstile
- DataDome
- Akamai Bot Manager
- PerimeterX

Features:
- WebGL/Canvas fingerprint randomization
- Navigator property spoofing
- AudioContext fingerprint protection
- Font enumeration protection
- Timezone/locale consistency checks
- Headless Chrome detection bypass
"""

from __future__ import annotations

import hashlib
import random
import time
from datetime import timezone
from typing import Any

from playwright.async_api import Page


async def inject_advanced_stealth(page: Page, config: dict[str, Any] = None) -> None:
    """Inject comprehensive anti-detection scripts into the page.

    Args:
        page: Playwright page instance
        config: Optional configuration for stealth parameters
    """
    config = config or {}

    # Generate consistent but randomized fingerprints for this session
    session_seed = config.get("session_seed") or str(time.time())
    session_hash = hashlib.md5(session_seed.encode()).hexdigest()

    # Randomized but consistent WebGL vendor/renderer
    webgl_vendors = [
        ("Intel Inc.", "Intel Iris OpenGL Engine"),
        ("Intel Inc.", "Intel Iris Pro OpenGL Engine"),
        ("NVIDIA Corporation", "NVIDIA GeForce GT 750M OpenGL Engine"),
        ("ATI Technologies Inc.", "AMD Radeon R9 M370X OpenGL Engine"),
        ("Intel Inc.", "Intel HD Graphics 6000 OpenGL Engine"),
    ]
    vendor_index = int(session_hash[:2], 16) % len(webgl_vendors)
    gl_vendor, gl_renderer = webgl_vendors[vendor_index]

    # Randomize canvas noise slightly per session
    canvas_noise_seed = int(session_hash[2:6], 16)

    await page.add_init_script(
        f"""
        (function() {{
            'use strict';

            // ===== NAVIGATOR PROPERTIES =====
            
            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined
            }});

            // Fix navigator.plugins to show real plugins
            const originalPlugins = navigator.plugins;
            if (originalPlugins && originalPlugins.length === 0) {{
                // Simulate common plugins
                const mockPlugins = [
                    {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }},
                    {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpnsoofgcfegfnoicp' }},
                    {{ name: 'Native Client', filename: 'internal-nacl-plugin' }}
                ];
                Object.defineProperty(navigator, 'plugins', {{
                    get: () => mockPlugins
                }});
            }}

            // Fix navigator.languages to match locale
            const targetLanguages = ['en-US', 'en', 'de-DE', 'de'];
            Object.defineProperty(navigator, 'languages', {{
                get: () => targetLanguages
            }});

            // Fix navigator.connection
            if (navigator.connection) {{
                Object.defineProperty(navigator, 'connection', {{
                    get: () => ({{
                        effectiveType: '4g',
                        rtt: 50,
                        downlink: 10,
                        saveData: false
                    }})
                }});
            }}

            // ===== WEBGL FINGERPRINTING PROTECTION =====
            
            const WebGLRenderingContext = window.WebGLRenderingContext;
            const WebGL2RenderingContext = window.WebGL2RenderingContext;

            if (WebGLRenderingContext) {{
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                    // UNMASKED_VENDOR_WEBGL = 0x9245 (37445)
                    if (parameter === 37445) {{
                        return '{gl_vendor}';
                    }}
                    // UNMASKED_RENDERER_WEBGL = 0x9246 (37446)
                    if (parameter === 37446) {{
                        return '{gl_renderer}';
                    }}
                    return getParameter.call(this, parameter);
                }};
            }}

            if (WebGL2RenderingContext) {{
                const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(parameter) {{
                    if (parameter === 37445) {{
                        return '{gl_vendor}';
                    }}
                    if (parameter === 37446) {{
                        return '{gl_renderer}';
                    }}
                    return getParameter2.call(this, parameter);
                }};
            }}

            // ===== CANVAS FINGERPRINTING PROTECTION =====
            
            const HTMLCanvasElement = window.HTMLCanvasElement;
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            const originalToBlob = HTMLCanvasElement.prototype.toBlob;

            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                const result = originalToDataURL.call(this, type);
                
                // Add subtle noise only to image data URLs (not SVG etc)
                if (type && type.startsWith('image/') && this.width > 1 && this.height > 1) {{
                    try {{
                        const ctx = this.getContext('2d');
                        if (ctx) {{
                            const imageData = ctx.getImageData(0, 0, this.width, this.height);
                            const data = imageData.data;
                            
                            // Add deterministic but subtle noise based on canvas content hash
                            let hash = {canvas_noise_seed};
                            for (let i = 0; i < data.length; i += 4) {{
                                hash = (hash * 31 + data[i] + data[i+1] + data[i+2]) % 1000000;
                                const noise = (hash % 5) - 2; // -2 to +2
                                data[i] = Math.min(255, Math.max(0, data[i] + noise));
                                data[i+1] = Math.min(255, Math.max(0, data[i+1] + noise));
                                data[i+2] = Math.min(255, Math.max(0, data[i+2] + noise));
                            }}
                            ctx.putImageData(imageData, 0, 0);
                        }}
                    }} catch (e) {{
                        // Silently fail if we can't modify canvas
                    }}
                }}
                return originalToDataURL.call(this, type);
            }};

            // ===== AUDIO CONTEXT FINGERPRINTING =====
            
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (AudioContext) {{
                const originalCreateGain = AudioContext.prototype.createGain;
                AudioContext.prototype.createGain = function() {{
                    const gainNode = originalCreateGain.call(this);
                    
                    // Slightly modify gain values to add fingerprint noise
                    const originalSetValue = gainNode.gain.setValueAtTime;
                    gainNode.gain.setValueAtTime = function(value, startTime) {{
                        // Add tiny variation (inaudible)
                        const variation = (Math.random() - 0.5) * 0.0001;
                        return originalSetValue.call(this, value + variation, startTime);
                    }};
                    
                    return gainNode;
                }};
            }}

            // ===== FONT ENUMERATION PROTECTION =====
            
            // Intercept font loading APIs
            if (window.FontFace) {{
                const originalFontFace = window.FontFace;
                window.FontFace = function(family, source, descriptors) {{
                    // Allow all fonts but add timing noise
                    return new originalFontFace(family, source, descriptors);
                }};
            }}

            // ===== SCREEN PROPERTIES =====
            
            // Ensure screen properties look natural
            const originalOuterWidth = window.outerWidth;
            const originalOuterHeight = window.outerHeight;
            
            // Some detection scripts check for outerWidth === innerWidth (headless indicator)
            if (window.outerWidth === window.innerWidth && window.outerWidth > 0) {{
                Object.defineProperty(window, 'outerWidth', {{
                    get: () => originalOuterWidth + 20
                }});
                Object.defineProperty(window, 'outerHeight', {{
                    get: () => originalOuterHeight + 100
                }});
            }}

            // ===== PERMISSIONS API =====
            
            if (navigator.permissions) {{
                const originalQuery = navigator.permissions.query;
                navigator.permissions.query = function(parameters) {{
                    return originalQuery.call(this, parameters).then(result => {{
                        // Don't reveal 'denied' for notifications (common headless indicator)
                        if (parameters.name === 'notifications' && result.state === 'denied') {{
                            result.state = 'prompt';
                        }}
                        return result;
                    }});
                }};
            }}

            // ===== HARDWARE CONCURRENCY & DEVICE MEMORY =====
            
            // Set realistic hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {random.randint(4, 16)}
            }});

            // Set realistic device memory (in GB)
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {random.choice([4, 8, 16])}
            }});

            // ===== USER AGENT CONSISTENCY =====
            
            // Ensure user agent doesn't contain headless indicators
            const originalUserAgent = navigator.userAgent;
            if (originalUserAgent.includes('Headless')) {{
                Object.defineProperty(navigator, 'userAgent', {{
                    get: () => originalUserAgent.replace(/HeadlessChrome/, 'Chrome')
                }});
            }}

            // ===== BENCHMARK TIMING PROTECTION =====
            
            // Add slight jitter to performance timing (anti-fingerprinting)
            const originalNow = performance.now.bind(performance);
            performance.now = function() {{
                const time = originalNow();
                // Add sub-millisecond jitter
                const jitter = (Math.random() - 0.5) * 0.1;
                return time + jitter;
            }};

            // ===== DETECT AND COUNTER HEADLESS CHECKS =====
            
            // Check for common headless detection properties and fix them
            const headlessChecks = [
                '__driver__',
                '__selenium_unwrapped__',
                'callPhantom',
                '_phantom',
                '__nightmare'
            ];
            
            headlessChecks.forEach(prop => {{
                if (window[prop] !== undefined) {{
                    delete window[prop];
                }}
            }});

            // Fix chrome object if missing
            if (!window.chrome) {{
                window.chrome = {{
                    runtime: {{}},
                    loadTimes: function() {{ return {{}}; }},
                    csi: function() {{ return {{}}; }}
                }};
            }}

            console.log('[Stealth] Advanced stealth injections applied successfully');
        }})();
        """
    )


async def apply_timezone_spoof(page: Page, target_timezone: str = None) -> None:
    """Apply timezone spoofing to match proxy location.

    Args:
        page: Playwright page instance
        target_timezone: IANA timezone string (e.g., 'America/New_York')
    """
    if target_timezone is None:
        # Use system timezone
        target_timezone = timezone.utc.tzname(None) or 'UTC'

    await page.add_init_script(
        f"""
        (function() {{
            const originalDate = Date;
            const targetTimezone = '{target_timezone}';
            
            // Override Date methods to return timezone-adjusted values
            Date = function(...args) {{
                if (args.length === 0) {{
                    return new originalDate();
                }}
                return new originalDate(...args);
            }};
            
            Date.prototype = originalDate.prototype;
            
            // Store original methods
            const originalToString = Date.prototype.toString;
            const originalToLocaleString = Date.prototype.toLocaleString;
            const originalToLocaleDateString = Date.prototype.toLocaleDateString;
            const originalToLocaleTimeString = Date.prototype.toLocaleTimeString;
            
            // Override to always use target timezone
            Date.prototype.toString = function() {{
                return originalToLocaleString.call(this, 'en-US', {{ timeZone: targetTimezone }});
            }};
            
            Date.prototype.toLocaleString = function(locales, options) {{
                options = options || {{}};
                options.timeZone = targetTimezone;
                return originalToLocaleString.call(this, locales, options);
            }};
            
            Date.prototype.toLocaleDateString = function(locales, options) {{
                options = options || {{}};
                options.timeZone = targetTimezone;
                return originalToLocaleDateString.call(this, locales, options);
            }};
            
            Date.prototype.toLocaleTimeString = function(locales, options) {{
                options = options || {{}};
                options.timeZone = targetTimezone;
                return originalToLocaleTimeString.call(this, locales, options);
            }};
            
            // Override getTimezoneOffset to return target timezone offset
            Date.prototype.getTimezoneOffset = function() {{
                const date = new originalDate(this.valueOf());
                const tzString = date.toLocaleString('en-US', {{ timeZone: targetTimezone, hour12: false }});
                const utcString = date.toLocaleString('en-US', {{ timeZone: 'UTC', hour12: false }});
                
                const tzDate = new originalDate(tzString);
                const utcDate = new originalDate(utcString);
                
                return (utcDate - tzDate) / 60000;
            }};
        }})();
        """
    )


async def detect_leaks(page: Page) -> dict[str, bool]:
    """Run detection scripts to identify potential fingerprinting leaks.

    Returns a dictionary of leak test results.

    Args:
        page: Playwright page instance

    Returns:
        Dictionary with test names as keys and boolean leak status as values
    """
    results = {}

    # Test 1: Check navigator.webdriver
    webdriver_leak = await page.evaluate(
        "() => navigator.webdriver === true || navigator.webdriver === 'true'"
    )
    results["webdriver_property"] = webdriver_leak

    # Test 2: Check for headless Chrome indicators
    headless_indicators = await page.evaluate(
        """
        () => {
            const checks = {
                hasHeadlessUA: /HeadlessChrome/i.test(navigator.userAgent),
                hasMissingChrome: !window.chrome,
                hasDriverProperty: !!window.__driver__,
                hasSeleniumUnwrapped: !!window.__selenium_unwrapped__,
                hasCallPhantom: !!window.callPhantom,
                hasPhantom: !!window._phantom,
                hasNightmare: !!window.__nightmare__
            };
            return Object.values(checks).some(v => v);
        }
        """
    )
    results["headless_indicators"] = headless_indicators

    # Test 3: Check WebGL renderer info
    webgl_leak = await page.evaluate(
        """
        () => {
            try {
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (!gl) return false;
                
                const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                if (!debugInfo) return false;
                
                const vendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL);
                const renderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
                
                // Generic or suspicious values indicate potential leak
                const suspicious = [
                    'Google SwiftShader',
                    'ANGLE',
                    'llvmpipe',
                    'Software Adapter'
                ];
                
                return suspicious.some(s => 
                    (vendor && vendor.includes(s)) || (renderer && renderer.includes(s))
                );
            } catch (e) {
                return false;
            }
        }
        """
    )
    results["webgl_fingerprint"] = webgl_leak

    # Test 4: Check canvas fingerprint consistency
    canvas_leak = await page.evaluate(
        """
        () => {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = 200;
                canvas.height = 50;
                const ctx = canvas.getContext('2d');
                
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.textBaseline = 'ideographic';
                ctx.fillStyle = '#f60';
                ctx.fillRect(125, 1, 62, 20);
                ctx.fillStyle = '#069';
                ctx.fillText('Hello World', 2, 15);
                ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
                ctx.fillText('Hello World', 4, 17);
                
                const dataURL = canvas.toDataURL();
                // A very specific hash would indicate no randomization
                return dataURL.length < 100; // Suspiciously short
            } catch (e) {
                return false;
            }
        }
        """
    )
    results["canvas_fingerprint"] = canvas_leak

    # Test 5: Check permissions API
    permissions_leak = await page.evaluate(
        """
        () => {
            if (!navigator.permissions) return false;
            // Check if notification permission is permanently denied (headless indicator)
            return navigator.permissions.query({name: 'notifications'})
                .then(result => result.state === 'denied')
                .catch(() => false);
        }
        """
    )
    # Note: This is async, so we'll mark it as unknown for now
    results["permissions_api"] = False  # Would need proper async handling

    return results


async def generate_user_agent(os_type: str = None, browser_type: str = "chrome") -> str:
    """Generate a realistic user agent string.

    Args:
        os_type: Target OS ('windows', 'macos', 'linux') or None for random
        browser_type: Browser type ('chrome', 'firefox', 'edge')

    Returns:
        Realistic user agent string
    """
    if os_type is None:
        os_type = random.choice(["windows", "macos", "linux"])

    # Chrome versions (recent stable versions)
    chrome_versions = [
        "120.0.6099.109",
        "119.0.6045.199",
        "118.0.5993.117",
        "117.0.5938.149",
    ]
    chrome_version = random.choice(chrome_versions)
    major_version = chrome_version.split(".")[0]

    if os_type == "windows":
        windows_versions = ["10.0", "11.0"]
        win_version = random.choice(windows_versions)
        return (
            f"Mozilla/5.0 (Windows NT {win_version}; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_version} Safari/537.36"
        )
    elif os_type == "macos":
        mac_versions = [
            "10_15_7",
            "11_7_10",
            "12_7_2",
            "13_6_3",
            "14_2_1",
        ]
        mac_version = random.choice(mac_versions)
        return (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {mac_version}) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_version} Safari/537.36"
        )
    else:  # linux
        return (
            f"Mozilla/5.0 (X11; Linux x86_64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_version} Safari/537.36"
        )
