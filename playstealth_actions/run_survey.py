"""Run-survey PlayStealth action."""

from __future__ import annotations


async def run(timeout_seconds: int, index: int, max_steps: int) -> int:
    from playstealth_cli import _run_survey_loop

    return await _run_survey_loop(timeout_seconds, index, max_steps)
