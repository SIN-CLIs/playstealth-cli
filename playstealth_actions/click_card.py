"""Click a survey card and follow any new tab."""

from __future__ import annotations

import asyncio


async def run(page, index: int):
    """Click one survey card and return the active page."""
    cards = page.locator("#survey_list .survey-item")
    count = await cards.count()
    if count == 0:
        raise RuntimeError("No survey cards found")

    best = cards.nth(min(index, count - 1))
    onclick = await best.get_attribute("onclick") or ""
    await best.scroll_into_view_if_needed(timeout=4000)
    try:
        await best.dispatch_event("click")
    except Exception:
        await best.click(timeout=4000, force=True)

    await asyncio.sleep(2)
    if "clickSurvey(" in onclick:
        sid = onclick.split("clickSurvey('")[1].split("')")[0]
        print(f"🧷 card onclick survey id: {sid}")
        result = await page.evaluate("sid => clickSurvey(sid)", sid)
        print(f"🧠 clickSurvey result: {result!r}")
        await asyncio.sleep(3)

    await asyncio.sleep(1)
    if len(page.context.pages) > 1:
        for candidate in reversed(page.context.pages):
            if not candidate.is_closed() and candidate.url != "about:blank":
                print(f"🪟 Active page after click: {candidate.url}")
                return candidate
        return page.context.pages[-1]
    return page
