# ================================================================================
# DATEI: exceptions.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Typed exception hierarchy for the HeyPiggy Vision Worker.

The legacy worker caught ``Exception`` in 34+ places, which swallowed bugs
and made control-flow reasoning impossible. The replacement code uses
narrow ``except`` clauses against the classes defined here.

Class hierarchy::

    WorkerError                      (root, never raise directly)
    ├── ConfigurationError           (bad env / missing required setting)
    ├── PreflightError               (startup sanity-check failed)
    ├── BridgeError                  (a2a bridge process / IPC)
    │   ├── BridgeTimeoutError
    │   ├── BridgeProtocolError
    │   └── BridgeUnavailableError
    ├── VisionError                  (NVIDIA NIM / image pipeline)
    │   ├── VisionTimeoutError
    │   ├── VisionRateLimitError
    │   └── VisionCircuitOpenError
    ├── ActionError                  (controller action execution)
    │   ├── ElementNotFoundError
    │   ├── ActionTimeoutError
    │   └── ActionBlockedError
    └── ShutdownRequested            (cooperative shutdown signal)
"""

from __future__ import annotations

from typing import Any


class WorkerError(Exception):
    """Root of the worker exception hierarchy.

    Attributes:
        context: Arbitrary structured context attached at raise-site;
            merged into log records by :mod:`worker.logging`.
    """

    def __init__(self, message: str, /, **context: Any) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = context

    def __str__(self) -> str:
        base = super().__str__()
        if not self.context:
            return base
        extras = " ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{base} [{extras}]"


# ---------------------------------------------------------------------------
# Config / preflight
# ---------------------------------------------------------------------------


class ConfigurationError(WorkerError):
    # ========================================================================
    # KLASSE: ConfigurationError(WorkerError)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Required configuration is missing or invalid."""


class PreflightError(WorkerError):
    """A startup preflight check failed and the worker cannot safely run."""


# ---------------------------------------------------------------------------
# Bridge (a2a subprocess / IPC)
# ---------------------------------------------------------------------------


class BridgeError(WorkerError):
    """Base class for a2a bridge failures."""


class BridgeTimeoutError(BridgeError):
    """A bridge call exceeded its allotted time."""


class BridgeProtocolError(BridgeError):
    """The bridge returned malformed or unexpected output."""


class BridgeUnavailableError(BridgeError):
    """The bridge process is not running or not reachable."""


# ---------------------------------------------------------------------------
# Vision (NVIDIA NIM)
# ---------------------------------------------------------------------------


class VisionError(WorkerError):
    """Base class for vision-pipeline failures."""


class VisionTimeoutError(VisionError):
    """The vision endpoint did not respond in time."""


class VisionRateLimitError(VisionError):
    """The vision endpoint returned HTTP 429 / quota exceeded."""


class VisionCircuitOpenError(VisionError):
    """The vision circuit breaker is open — calls short-circuited."""


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


class ActionError(WorkerError):
    """Base class for controller action failures."""


class ElementNotFoundError(ActionError):
    """Target DOM element was not found on the page."""


class ActionTimeoutError(ActionError):
    """An action (click / type / wait) exceeded its deadline."""


class ActionBlockedError(ActionError):
    """The action was refused (e.g. modal overlay, preflight veto)."""


# ---------------------------------------------------------------------------
# Sitepack (site-specific selector packs)
#
# Sitepack errors are action-adjacent but distinct: they mean the pack
# configuration itself is wrong (SitepackValidationError) or references a
# selector/flow/page-signature that is not declared in the active pack
# (SelectorNotFoundError). They are imported by worker.sitepack and the
# corresponding tests; adding them here closes the half-merged refactor
# that broke the CI test collection step.
# ---------------------------------------------------------------------------


class SitepackValidationError(ActionError):
    # ========================================================================
    # KLASSE: SitepackValidationError(ActionError)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """The sitepack payload failed structural validation.

    Raised when the JSON is not an object, required top-level keys are
    missing or empty, ``flows`` entries are not lists of URLs, etc.
    """


class SelectorNotFoundError(ActionError):
    """A selector key was requested that is not declared in the sitepack."""


# ---------------------------------------------------------------------------
# Cooperative shutdown
# ---------------------------------------------------------------------------


class ShutdownRequested(WorkerError):
    """Raised by signal handlers to unwind loops cooperatively.

    Callers should treat this as a *normal* exit, not a failure — catch it
    at the top of the main loop and return cleanly.
    """


__all__ = [
    "ActionBlockedError",
    "ActionError",
    "ActionTimeoutError",
    "BridgeError",
    "BridgeProtocolError",
    "BridgeTimeoutError",
    "BridgeUnavailableError",
    "ConfigurationError",
    "ElementNotFoundError",
    "PreflightError",
    "SelectorNotFoundError",
    "ShutdownRequested",
    "SitepackValidationError",
    "VisionCircuitOpenError",
    "VisionError",
    "VisionRateLimitError",
    "VisionTimeoutError",
    "WorkerError",
]
