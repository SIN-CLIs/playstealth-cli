"""Bridge contract v1 -- mirror of OpenSIN-Bridge/extension/src/contract/v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BRIDGE_CONTRACT_VERSION = "1.0.0"
BRIDGE_CONTRACT_REVISION = 1

RetryHint = Literal["retry", "retry-after-refresh", "retry-after-reauth", "abort"]


@dataclass(frozen=True)
class BridgeMethod:
    name: str
    category: str
    idempotent: bool
    mutates: bool
    retry_hint: RetryHint
    description: str


METHODS: tuple[BridgeMethod, ...] = (
    # ----- contract meta -----------------------------------------------------
    BridgeMethod("bridge.contract", "meta", True, False, "abort", "Return the active bridge contract"),
    BridgeMethod("bridge.contract.version", "meta", True, False, "abort", "Return contract version + revision"),
    BridgeMethod("bridge.contract.method", "meta", True, False, "abort", "Metadata for a single method"),
    BridgeMethod("bridge.contract.idempotent", "meta", True, False, "abort", "Is the method retry-safe?"),
    BridgeMethod("bridge.contract.translate", "meta", True, False, "abort", "Translate internal error -> public code"),
    # ----- tabs --------------------------------------------------------------
    BridgeMethod("tabs.list", "tabs", True, False, "retry", "List all open tabs"),
    BridgeMethod("tabs.focus", "tabs", True, True, "retry", "Focus a tab"),
    BridgeMethod("tabs.close", "tabs", False, True, "abort", "Close a tab"),
    BridgeMethod("tabs.open", "tabs", False, True, "retry", "Open a new tab"),
    # ----- navigation --------------------------------------------------------
    BridgeMethod("navigation.to", "navigation", False, True, "retry-after-refresh", "Navigate to URL"),
    BridgeMethod("navigation.back", "navigation", False, True, "retry", "Back"),
    BridgeMethod("navigation.forward", "navigation", False, True, "retry", "Forward"),
    BridgeMethod("navigation.reload", "navigation", False, True, "retry", "Reload"),
    # ----- dom ---------------------------------------------------------------
    BridgeMethod("dom.snapshot", "dom", True, False, "retry", "AX snapshot of a tab"),
    BridgeMethod("dom.query", "dom", True, False, "retry", "Query by selector"),
    BridgeMethod("dom.click", "dom", False, True, "retry-after-refresh", "Click by selector or target"),
    BridgeMethod("dom.type", "dom", False, True, "retry-after-refresh", "Type into input"),
    BridgeMethod("dom.scroll", "dom", False, True, "retry", "Scroll element or page"),
    BridgeMethod("dom.evaluate", "dom", False, False, "retry", "Evaluate JS in page"),
    # ----- cookies -----------------------------------------------------------
    BridgeMethod("cookies.get", "cookies", True, False, "retry", "Get cookies by URL"),
    BridgeMethod("cookies.set", "cookies", True, True, "retry", "Set a cookie"),
    BridgeMethod("cookies.remove", "cookies", True, True, "retry", "Remove a cookie"),
    # ----- storage -----------------------------------------------------------
    BridgeMethod("storage.local.get", "storage", True, False, "retry", "Read localStorage"),
    BridgeMethod("storage.local.set", "storage", True, True, "retry", "Write localStorage"),
    BridgeMethod("storage.session.get", "storage", True, False, "retry", "Read sessionStorage"),
    BridgeMethod("storage.session.set", "storage", True, True, "retry", "Write sessionStorage"),
    # ----- network -----------------------------------------------------------
    BridgeMethod("network.lastRequests", "network", True, False, "retry", "Last N network requests"),
    BridgeMethod("network.capture.start", "network", True, True, "retry", "Start capture"),
    BridgeMethod("network.capture.stop", "network", True, True, "retry", "Stop capture"),
    # ----- session lifecycle (issue #71) -------------------------------------
    BridgeMethod("session.manifest", "session", True, True, "retry", "Build or refresh session manifest"),
    BridgeMethod("session.invalidate", "session", True, True, "abort", "Invalidate session"),
    BridgeMethod("session.lastKnownGood", "session", True, False, "retry", "Get last-known-good snapshot"),
    BridgeMethod("session.health", "session", True, False, "retry", "Probe session status"),
    BridgeMethod("session.list", "session", True, False, "retry", "List manifests"),
    BridgeMethod("session.drop", "session", True, True, "abort", "Drop a manifest"),
    BridgeMethod("session.save", "session", False, True, "retry", "Save cookies + storage snapshot"),
    BridgeMethod("session.restore", "session", False, True, "retry-after-refresh", "Restore snapshot"),
    # ----- behavior (issue #70) ---------------------------------------------
    BridgeMethod("behavior.start", "behavior", False, True, "retry", "Start recording"),
    BridgeMethod("behavior.stop", "behavior", False, True, "retry", "Stop recording"),
    BridgeMethod("behavior.status", "behavior", True, False, "retry", "Recording status"),
    BridgeMethod("behavior.timeline", "behavior", True, False, "retry", "Behavior timeline"),
    BridgeMethod("bridge.evidenceBundle", "observability", True, False, "retry", "Assemble evidence bundle"),
    BridgeMethod("bridge.traces", "observability", True, False, "retry", "Recent dispatches"),
    # ----- stealth (issue #74) ----------------------------------------------
    BridgeMethod("stealth.assess", "stealth", True, False, "retry", "Score environment coherence"),
    BridgeMethod("stealth.detectChallenge", "stealth", True, False, "retry", "Detect anti-bot challenge"),
    # ----- vision ------------------------------------------------------------
    BridgeMethod("vision.screenshot", "vision", True, False, "retry", "Screenshot the tab"),
)

_METHODS_BY_NAME = {m.name: m for m in METHODS}


def get_method(name: str) -> BridgeMethod:
    try:
        return _METHODS_BY_NAME[name]
    except KeyError as exc:
        raise BridgeError("METHOD_NOT_FOUND", f"unknown bridge method: {name}", retry_hint="abort") from exc


def is_idempotent(name: str) -> bool:
    return get_method(name).idempotent


def retry_hint_for(name: str) -> RetryHint:
    return get_method(name).retry_hint


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

ERROR_CODES: tuple[str, ...] = (
    "INVALID_ARGS",
    "METHOD_NOT_FOUND",
    "TAB_NOT_FOUND",
    "TAB_CLOSED",
    "SESSION_INVALID",
    "SESSION_EXPIRED",
    "SESSION_MISSING",
    "TARGET_NOT_FOUND",
    "STALE_TARGET",
    "NAV_TIMEOUT",
    "CHALLENGE_DETECTED",
    "STEALTH_INCOHERENT",
    "RATE_LIMITED",
    "PERMISSION_DENIED",
    "INTERNAL",
    "CONTRACT_MISMATCH",
)


class BridgeError(Exception):
    """Canonical bridge error. Mirrors the JS ``BridgeError`` class."""

    def __init__(self, code: str, message: str, *, retry_hint: RetryHint = "abort", data: dict | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown bridge error code: {code}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retry_hint = retry_hint
        self.data = data or {}

    def to_wire(self) -> dict:
        return {"code": self.code, "message": str(self), "retry_hint": self.retry_hint, "data": self.data}


class ContractMismatch(BridgeError):
    def __init__(self, expected: str, got: str):
        super().__init__(
            "CONTRACT_MISMATCH",
            f"bridge contract major mismatch: expected {expected}, got {got}",
            retry_hint="abort",
            data={"expected": expected, "got": got},
        )


def classify_error(raw: dict | Exception) -> BridgeError:
    """Turn any bridge response or exception into a BridgeError."""
    if isinstance(raw, BridgeError):
        return raw
    if isinstance(raw, Exception):
        return BridgeError("INTERNAL", str(raw), retry_hint="retry")
    code = raw.get("code") if isinstance(raw, dict) else None
    if code and code in ERROR_CODES:
        return BridgeError(
            code,
            raw.get("message") or code,
            retry_hint=raw.get("retry_hint", "abort"),
            data=raw.get("data") or {},
        )
    return BridgeError("INTERNAL", str(raw), retry_hint="retry")
