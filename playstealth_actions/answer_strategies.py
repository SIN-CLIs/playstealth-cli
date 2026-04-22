"""Survey answer strategies for PlayStealth.

This module keeps strategy selection explicit and auditable. The CLI can switch
between random, consistent, and persona-aware behavior without rewriting the
actual survey flow.
"""

from __future__ import annotations

import hashlib
import random
from abc import ABC, abstractmethod
from typing import Any

from playstealth_actions.persona_manager import DEFAULT_PERSONA, get_persona


def _persona_heuristic_indices(
    question: str, options: list[str], persona: dict[str, Any]
) -> list[int]:
    """Return persona-driven preferred indices for a question when obvious."""
    q = question.lower()
    lowered = [option.lower() for option in options]

    if "zigaretten" in q or "rauche" in q:
        for idx, option in enumerate(lowered):
            if "ich rauche keine zigaretten" in option or "nichts des oben genannten" in option:
                return [idx]

    gender = str(persona.get("gender", "male")).lower()
    if "geschlecht" in q or "gender" in q:
        target = "männlich" if gender == "male" else "weiblich"
        for idx, option in enumerate(lowered):
            if target in option:
                return [idx]

    age = str(persona.get("age", ""))
    if age and "alter" in q:
        for idx, option in enumerate(lowered):
            if age in option:
                return [idx]

    interests = [str(item).lower() for item in persona.get("interests", [])]
    if interests and ("interessen" in q or "hobb" in q):
        matches = [
            idx
            for idx, option in enumerate(lowered)
            if any(interest in option for interest in interests)
        ]
        if matches:
            return matches[:3]

    return []


class BaseStrategy(ABC):
    """Base class for answer selection strategies."""

    @abstractmethod
    async def choose(self, question: str, option_count: int, options: list[str]) -> int:
        """Return the preferred option index."""


class RandomStrategy(BaseStrategy):
    """Choose answers completely at random."""

    async def choose(self, question: str, option_count: int, options: list[str]) -> int:
        if option_count == 0:
            return 0
        return random.randint(0, option_count - 1)


class ConsistentStrategy(BaseStrategy):
    """Choose the same option index every time."""

    def __init__(self, fixed_index: int = 1) -> None:
        self.fixed_index = fixed_index

    async def choose(self, question: str, option_count: int, options: list[str]) -> int:
        if option_count == 0:
            return 0
        return min(self.fixed_index, max(0, option_count - 1))


class PersonaStrategy(BaseStrategy):
    """Choose stable, persona-aware answers.

    The strategy first tries explicit persona heuristics and then falls back to a
    deterministic hash-based choice so repeated questions stay consistent.
    """

    def __init__(self, persona: str = "default") -> None:
        self.persona_name = persona
        self.persona = get_persona(persona) if persona != "default" else DEFAULT_PERSONA

    async def choose(self, question: str, option_count: int, options: list[str]) -> int:
        if option_count == 0:
            return 0

        heuristics = _persona_heuristic_indices(question, options, self.persona)
        if heuristics:
            return heuristics[0]

        question_hash = int(hashlib.md5(question.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(question_hash)
        return rng.randint(0, option_count - 1)


def get_strategy(name: str, **kwargs) -> BaseStrategy:
    """Create a strategy instance by name."""
    strategies = {
        "random": RandomStrategy,
        "consistent": ConsistentStrategy,
        "persona": PersonaStrategy,
    }
    cls = strategies.get(name)
    if not cls:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(strategies.keys())}")
    return cls(**kwargs)
