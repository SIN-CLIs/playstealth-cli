"""Shared diagnostics helpers for PlayStealth.

These routines expose useful operational summaries without forcing callers to
know where telemetry, trap detection, or page inspection logic lives.
"""

from __future__ import annotations

from playstealth_actions.ban_risk_monitor import calculate_ban_risk
from playstealth_actions.telemetry import get_summary
from playstealth_actions.trap_detector import analyze_page_traps


async def inspect_page(page) -> dict[str, object]:
    """Return a small summary of the active page."""
    return {
        "url": page.url,
        "title": await page.title(),
        "body": (await page.locator("body").inner_text(timeout=3000))[:1000],
    }


async def detect_traps(page) -> dict[str, object]:
    """Run trap analysis against the current modal/page."""
    modal = page.locator("#survey-modal")
    question_text = ""
    try:
        question_text = (await modal.inner_text(timeout=2000)).strip()
    except Exception:
        pass
    options = await modal.locator("label[name='answerOption']").all_inner_texts()
    return await analyze_page_traps(page, question_text, options)


def telemetry_summary() -> dict[str, object]:
    """Return the current telemetry summary."""
    return get_summary()


def ban_risk_summary(session_id: str | None = None) -> dict[str, object]:
    """Return the current ban-risk estimate."""
    return calculate_ban_risk(session_id)
