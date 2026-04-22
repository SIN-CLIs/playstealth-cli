"""Handle survey questions in PlayStealth.

This module stays tiny on purpose: it focuses on the common layouts we see
in production and delegates page selection to shared helpers.
"""

from __future__ import annotations

from playstealth_actions.question_router import run as question_router_run


async def run(page, option_index: int):
    """Answer one modal question and return the active page."""
    return await question_router_run(page, option_index)
