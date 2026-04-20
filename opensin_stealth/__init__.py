"""Stealth strategy registry -- issue #74."""
from opensin_stealth.strategy import (
    StealthStrategy,
    PassiveStrategy,
    HumanizedStrategy,
    register,
    registry,
    select,
)

__all__ = [
    "StealthStrategy",
    "PassiveStrategy",
    "HumanizedStrategy",
    "register",
    "registry",
    "select",
]
