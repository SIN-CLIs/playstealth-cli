"""Click-survey PlayStealth action."""

from __future__ import annotations


async def run(timeout_seconds: int, index: int) -> int:
    from playstealth_cli import _run_click_survey

    return await _run_click_survey(timeout_seconds, index)
