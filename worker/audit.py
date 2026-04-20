# ================================================================================
# DATEI: audit.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Append-only JSONL audit log for compliance + debugging.

Every security-relevant event (auth attempts, worker takeovers, vision
fallbacks, answer submissions) is appended here in addition to the normal
structured log. The audit log is never rotated from within the worker —
operators are expected to ship it to a SIEM.

Design guarantees:

* **Append-only** — writes use ``O_APPEND`` flush-per-line semantics.
* **Fail-safe** — errors during audit writes are logged but never raised,
  so a broken disk does not break the worker.
* **Deterministic schema** — every record has ``ts``, ``run_id``,
  ``event``, and ``payload`` keys exactly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from worker.logging import get_logger, get_run_id

_log = get_logger(__name__)

_SCHEMA_VERSION: Final[str] = "1"


class AuditLogger:
    # ========================================================================
    # KLASSE: AuditLogger
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """JSONL audit-log sink.

    Instances are cheap to construct and reusable across the life of a run.
    Thread-safe only under cooperative asyncio (one event loop).
    """

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Absolute path to the underlying audit log file."""
        return self._path

    def emit(self, event: str, /, **payload: Any) -> None:
        """Append a single audit record. Never raises."""
        record: dict[str, Any] = {
            "v": _SCHEMA_VERSION,
            "ts": datetime.now(tz=UTC).isoformat(timespec="milliseconds"),
            "run_id": get_run_id(),
            "event": event,
            "payload": payload,
        }
        try:
            line = json.dumps(record, separators=(",", ":"), default=_fallback_encoder)
        except (TypeError, ValueError) as exc:
            _log.error(
                "audit_serialize_failed",
                audit_event=event,
                error=type(exc).__name__,
                error_message=str(exc),
            )
            return
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            _log.error(
                "audit_write_failed",
                audit_event=event,
                path=str(self._path),
                error=type(exc).__name__,
                error_message=str(exc),
            )


def _fallback_encoder(obj: Any) -> Any:
    """Best-effort encoder for unusual payload types."""
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except TypeError:
            pass
    return repr(obj)


__all__ = ["AuditLogger"]
