"""Open-list PlayStealth action."""

from __future__ import annotations


async def run(timeout_seconds: int) -> int:
    from playstealth_cli import _run_open_list

    return await _run_open_list(timeout_seconds)
