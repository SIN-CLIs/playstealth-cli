"""Handle the consent modal after a survey opens in a new tab."""

from __future__ import annotations

import asyncio

from playstealth_actions.page_utils import resolve_active_page


async def run(page):
    """Accept the consent modal and return the active page."""
    body = (await page.locator("body").inner_text(timeout=3000)).lower()
    if "einwilligung" not in body and "zustimmen" not in body:
        return page

    buttons = [
        "button:has-text('Zustimmen und fortfahren')",
        "button:has-text('Zustimmen')",
        "button:has-text('Fortfahren')",
    ]
    for selector in buttons:
        btn = page.locator(selector)
        if await btn.count() > 0:
            try:
                await btn.first.evaluate("el => el.click()")
            except Exception:
                await btn.first.click(force=True)
            await asyncio.sleep(2)
            print(f"✅ Consent clicked via {selector}")
            return await resolve_active_page(page)

    raise RuntimeError("Consent modal found but no consent button matched")
