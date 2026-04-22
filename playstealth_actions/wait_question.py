"""Wait for question state transition."""

from __future__ import annotations

import asyncio


async def run(page, timeout_seconds: int = 10) -> bool:
    """Wait until the consent modal turns into a real question or iframe loads."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            modal_text = (await page.locator("#survey-modal").inner_text(timeout=1000)).lower()
        except Exception:
            modal_text = ""
        try:
            iframe_src = await page.locator("iframe#frameurl").get_attribute("src") or ""
        except Exception:
            iframe_src = ""
        if iframe_src.strip():
            print(f"🧩 frameurl src became active: {iframe_src!r}")
            return True
        if "umfrage starten" not in modal_text and "du kannst jetzt" not in modal_text:
            print(f"🧩 survey-modal changed: {modal_text[:200]!r}")
            return True
        await asyncio.sleep(1)
    return False
