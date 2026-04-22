"""Survey list helpers for PlayStealth."""

from __future__ import annotations

from playwright.async_api import Page

from playwright_stealth_worker import _score_open_survey_candidate


async def print_cards(page: Page, context) -> list[dict[str, str]]:
    """Print the visible survey cards and return scored metadata."""
    if page.is_closed() and context.pages:
        page = context.pages[0]
    body_text = await page.locator("body").inner_text(timeout=3000)
    print(f"🧾 Body: {body_text[:250]!r}")
    cards = page.locator("#survey_list .survey-item")
    count = await cards.count()
    print(f"🪪 Survey cards: {count}")
    scored: list[dict[str, str]] = []
    for i in range(min(count, 20)):
        card = cards.nth(i)
        if not await card.is_visible():
            continue
        score, meta = await _score_open_survey_candidate(card)
        print(f"🎯 Card[{i}]: score={score} text={meta['text'][:90]!r}")
        scored.append({"index": str(i), "score": str(score), **meta})
    return scored
