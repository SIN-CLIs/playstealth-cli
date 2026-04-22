"""Shared helper for not-yet-implemented PlayStealth tools."""

from __future__ import annotations


async def run(*_args, **_kwargs):
    """Raise a clear error for planned-but-not-yet-built tools."""
    raise NotImplementedError("This PlayStealth tool is planned but not implemented yet")
