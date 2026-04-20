"""Pluggable stealth strategies.

A strategy owns two decisions:

* ``pre_action`` -- called before every interaction. May return a delay,
  a jitter, or raise to abort the action.
* ``on_challenge`` -- called when the bridge's ``stealth.detectChallenge``
  returns a non-empty challenge type.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol


class StealthStrategy(Protocol):
    name: str

    async def pre_action(self, ctx: dict[str, Any]) -> dict[str, Any]: ...
    async def on_challenge(self, ctx: dict[str, Any]) -> str: ...


@dataclass
class PassiveStrategy:
    """Does nothing. Baseline for tests and non-hostile sites."""

    name: str = "passive"

    async def pre_action(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {"delay_ms": 0}

    async def on_challenge(self, ctx: dict[str, Any]) -> str:
        # Passive means: we don't try to solve anything, we tell the
        # runtime to surface the challenge.
        return "surface"


@dataclass
class HumanizedStrategy:
    """Adds small random delays + verifies stealth.assess coherence score."""

    name: str = "humanized"
    min_ms: int = 120
    max_ms: int = 480
    min_coherence: float = 0.6

    async def pre_action(self, ctx: dict[str, Any]) -> dict[str, Any]:
        bridge = ctx.get("bridge")
        if bridge is not None:
            assessment = await bridge("stealth.assess", tabId=ctx.get("tab_id"))
            if assessment and assessment.get("coherence", 1.0) < self.min_coherence:
                return {
                    "delay_ms": random.randint(self.min_ms, self.max_ms),
                    "abort": True,
                    "reason": "stealth incoherent",
                    "assessment": assessment,
                }
        return {"delay_ms": random.randint(self.min_ms, self.max_ms)}

    async def on_challenge(self, ctx: dict[str, Any]) -> str:
        return "retreat"


_REGISTRY: dict[str, StealthStrategy] = {
    "passive": PassiveStrategy(),
    "humanized": HumanizedStrategy(),
}


def registry() -> dict[str, StealthStrategy]:
    return dict(_REGISTRY)


def select(name: str) -> StealthStrategy:
    if name not in _REGISTRY:
        raise KeyError(f"unknown stealth strategy: {name}")
    return _REGISTRY[name]


def register(strategy: StealthStrategy) -> None:
    _REGISTRY[strategy.name] = strategy
