"""Shared PlayStealth page/tab helpers.

These helpers keep the survey flow sane when HeyPiggy opens a new tab,
redirects to a consent page, or swaps the active frame/page.
"""

from __future__ import annotations


async def resolve_active_page(page):
    """Return the newest non-blank page in the current browser context."""
    pages = [candidate for candidate in page.context.pages if not candidate.is_closed()]
    if not pages:
        return page

    # Prefer the newest real URL — HeyPiggy often opens a second tab and leaves
    # the original dashboard tab in place.
    for candidate in reversed(pages):
        if candidate.url and candidate.url != "about:blank":
            if candidate != page:
                print(f"🪟 Switching to active page: {candidate.url}")
            return candidate

    return pages[-1]


async def page_overview(page) -> dict[str, object]:
    """Return a small, structured snapshot for logging and debugging."""
    return {
        "url": page.url,
        "closed": page.is_closed(),
        "tab_count": len(page.context.pages),
    }
