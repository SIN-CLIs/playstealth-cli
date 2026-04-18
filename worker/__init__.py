"""HeyPiggy Vision Worker — modular, typed, enterprise-grade worker package.

This package replaces the legacy ``heypiggy_vision_worker.py`` monolith.
The monolith is kept as a thin backward-compat shim that imports from here.

Public API is intentionally small — real consumers use :mod:`worker.cli`
or :func:`worker.loop.run_worker`. Everything else is internal.
"""

from __future__ import annotations

from worker._version import __version__
from worker.context import WorkerContext
from worker.exceptions import (
    ActionError,
    BridgeError,
    ConfigurationError,
    PreflightError,
    VisionError,
    WorkerError,
)

__all__ = [
    "ActionError",
    "BridgeError",
    "ConfigurationError",
    "PreflightError",
    "VisionError",
    "WorkerContext",
    "WorkerError",
    "__version__",
]
