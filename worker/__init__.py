# ================================================================================
# DATEI: __init__.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""HeyPiggy Vision Worker — modular, typed, enterprise-grade worker package.

This package replaces the legacy ``heypiggy_vision_worker.py`` monolith.
The monolith is kept as a thin backward-compat shim that imports from here.

Public API::

    from worker import (
        WorkerContext,
        run_worker,
        configure_logging,
        get_logger,
        retry,
        RetryPolicy,
        AuditLogger,
        ShutdownController,
        __version__,
    )

Everything not listed in :data:`__all__` is considered internal and may
change without notice.
"""

from __future__ import annotations

from worker._version import __version__
from worker.audit import AuditLogger
from worker.context import WorkerContext, current_context
from worker.exceptions import (
    ActionBlockedError,
    ActionError,
    ActionTimeoutError,
    BridgeError,
    BridgeProtocolError,
    BridgeTimeoutError,
    BridgeUnavailableError,
    ConfigurationError,
    ElementNotFoundError,
    PreflightError,
    ShutdownRequested,
    VisionCircuitOpenError,
    VisionError,
    VisionRateLimitError,
    VisionTimeoutError,
    WorkerError,
)
from worker.logging import configure_logging, get_logger
from worker.loop import run_worker
from worker.retry import RetryPolicy, retry
from worker.shutdown import ShutdownController

__all__ = [
    "ActionBlockedError",
    "ActionError",
    "ActionTimeoutError",
    "AuditLogger",
    "BridgeError",
    "BridgeProtocolError",
    "BridgeTimeoutError",
    "BridgeUnavailableError",
    "ConfigurationError",
    "ElementNotFoundError",
    "PreflightError",
    "RetryPolicy",
    "ShutdownController",
    "ShutdownRequested",
    "VisionCircuitOpenError",
    "VisionError",
    "VisionRateLimitError",
    "VisionTimeoutError",
    "WorkerContext",
    "WorkerError",
    "__version__",
    "configure_logging",
    "current_context",
    "get_logger",
    "retry",
    "run_worker",
]
