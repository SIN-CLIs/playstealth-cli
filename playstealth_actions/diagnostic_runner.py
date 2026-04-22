"""Browser-backed runner for diagnostics tools."""

from __future__ import annotations

from playstealth_actions.browser_bootstrap import open_browser
from playstealth_actions.diagnostic_common import (
    detect_consent,
    detect_iframe,
    detect_new_tab,
    detect_popup,
    detect_question_type,
    detect_spinner,
    dump_state,
    inspect_controls,
    inspect_page,
    inspect_tabs,
)
from playstealth_actions.stealth_enhancer import detect_leaks, inject_advanced_stealth


async def run(tool: str, timeout_seconds: int = 300) -> int:
    """Open the browser and run a diagnostics tool."""
    if tool == "dump-state":
        print(dump_state())
        return 0

    playwright, context, page = await open_browser()
    try:
        # Apply advanced stealth before running checks
        await inject_advanced_stealth(page)

        if tool == "detect-popup":
            print(await detect_popup(page))
        elif tool == "detect-new-tab":
            print(await detect_new_tab(page))
        elif tool == "detect-iframe":
            print(await detect_iframe(page))
        elif tool == "detect-spinner":
            print(await detect_spinner(page))
        elif tool == "detect-consent":
            print(await detect_consent(page))
        elif tool == "inspect-page":
            print(await inspect_page(page))
        elif tool == "inspect-tabs":
            print(await inspect_tabs(page))
        elif tool == "inspect-controls":
            print(await inspect_controls(page))
        elif tool == "detect-question-type":
            print(await detect_question_type(page))
        elif tool == "check-webgl":
            leaks = await detect_leaks(page)
            print("=== WebGL & Canvas Leak Check ===")
            print(f"WebGL Fingerprint Leak: {'⚠️ YES' if leaks.get('webgl_fingerprint') else '✅ NO'}")
            print(f"Canvas Fingerprint Leak: {'⚠️ YES' if leaks.get('canvas_fingerprint') else '✅ NO'}")
            return 0 if not any(leaks.values()) else 1
        elif tool == "check-timezone":
            tz_info = await page.evaluate(
                """
                () => ({
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    offset: new Date().getTimezoneOffset(),
                    language: navigator.language,
                    languages: navigator.languages
                })
                """
            )
            print("=== Timezone & Locale Check ===")
            print(f"Timezone: {tz_info['timezone']}")
            print(f"UTC Offset: {tz_info['offset'] / -60} hours")
            print(f"Primary Language: {tz_info['language']}")
            print(f"All Languages: {tz_info['languages']}")
            return 0
        elif tool == "check-headless":
            leaks = await detect_leaks(page)
            print("=== Headless Detection Check ===")
            print(f"WebDriver Property Leak: {'⚠️ YES' if leaks.get('webdriver_property') else '✅ NO'}")
            print(f"Headless Indicators: {'⚠️ YES' if leaks.get('headless_indicators') else '✅ NO'}")
            print(f"Permissions API Leak: {'⚠️ YES' if leaks.get('permissions_api') else '✅ NO'}")

            # Additional headless checks
            chrome_check = await page.evaluate("() => !!window.chrome")
            print(f"Chrome Object Present: {'✅ YES' if chrome_check else '⚠️ NO'}")

            overall_leak = any(leaks.values()) or not chrome_check
            print(f"\nOverall Status: {'⚠️ POTENTIALLY DETECTABLE' if overall_leak else '✅ LOOKS NATURAL'}")
            return 0 if not overall_leak else 1
        elif tool == "check-stealth":
            # Comprehensive stealth check
            leaks = await detect_leaks(page)
            print("=== Comprehensive Stealth Check ===\n")

            # Navigator checks
            nav_info = await page.evaluate(
                """
                () => ({
                    userAgent: navigator.userAgent,
                    webdriver: navigator.webdriver,
                    languages: navigator.languages,
                    platform: navigator.platform,
                    hardwareConcurrency: navigator.hardwareConcurrency,
                    deviceMemory: navigator.deviceMemory,
                    plugins: navigator.plugins.length
                })
                """
            )
            print("Navigator Properties:")
            print(f"  User Agent: {nav_info['userAgent'][:80]}...")
            print(f"  WebDriver: {nav_info['webdriver']}")
            print(f"  Languages: {nav_info['languages']}")
            print(f"  Platform: {nav_info['platform']}")
            print(f"  Hardware Concurrency: {nav_info['hardwareConcurrency']}")
            print(f"  Device Memory: {nav_info['deviceMemory']} GB")
            print(f"  Plugins: {nav_info['plugins']}")
            print()

            # Leak summary
            print("Leak Detection Results:")
            print(f"  WebDriver Property: {'⚠️ LEAKED' if leaks.get('webdriver_property') else '✅ CLEAN'}")
            print(f"  Headless Indicators: {'⚠️ DETECTED' if leaks.get('headless_indicators') else '✅ CLEAN'}")
            print(f"  WebGL Fingerprint: {'⚠️ SUSPICIOUS' if leaks.get('webgl_fingerprint') else '✅ CLEAN'}")
            print(f"  Canvas Fingerprint: {'⚠️ SUSPICIOUS' if leaks.get('canvas_fingerprint') else '✅ CLEAN'}")
            print()

            # Overall assessment
            critical_leaks = (
                leaks.get("webdriver_property")
                or leaks.get("headless_indicators")
                or not chrome_check
            )
            if critical_leaks:
                print("❌ CRITICAL: Your browser is likely detectable as automated!")
                return 1
            else:
                print("✅ GOOD: Basic stealth properties look natural.")
                print("\nNote: For production use, also test against:")
                print("  - https://bot.sannysoft.com/")
                print("  - https://abrahamjuliot.github.io/creepjs/")
                print("  - https://pixelscan.net/")
                return 0
        else:
            raise ValueError(f"Unsupported diagnostic tool: {tool}")
        return 0
    finally:
        await context.close()
        await playwright.stop()
