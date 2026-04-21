"""Answer-survey PlayStealth action."""

from __future__ import annotations


async def run(timeout_seconds: int, index: int, option_index: int) -> int:
    from playstealth_cli import _run_answer_survey

    return await _run_answer_survey(timeout_seconds, index, option_index)
