"""Shared browser bootstrap for PlayStealth.

Keeps all Playwright launch/profile details out of the CLI.
"""

from __future__ import annotations

from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

from playwright_stealth_worker import detect_chrome_profile_dir, prepare_playwright_user_data_dir

WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 768
HEYPIGGY_URL = "https://www.heypiggy.com/login?page=dashboard"


async def open_browser():
    """Open the persistent Playwright browser and land on HeyPiggy."""
    profile_root = prepare_playwright_user_data_dir()
    profile_dir = detect_chrome_profile_dir()

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_root),
        channel="chrome",
        headless=False,
        args=[
            f"--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            f"--profile-directory={profile_dir}",
        ],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.set_viewport_size({"width": WINDOW_WIDTH, "height": WINDOW_HEIGHT})

    stealth = Stealth()
    await stealth.apply_stealth_async(page)
    await page.goto(HEYPIGGY_URL)
    await page.wait_for_load_state("domcontentloaded")
    return playwright, context, page
