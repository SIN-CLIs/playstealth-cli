"""Metrics & telemetry for PlayStealth.

We keep telemetry intentionally small and anonymous: no full DOMs, no answers,
no credentials. Only execution metrics that help us understand reliability and
ban risk.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def telemetry_dir() -> Path:
    """Return the configured telemetry directory and create it if needed."""
    path = Path(os.getenv("PLAYSTEALTH_STATE_DIR", ".playstealth_state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def telemetry_file() -> Path:
    """Return the JSONL file used for append-only event logging."""
    return telemetry_dir() / "telemetry.jsonl"


# Compatibility exports for older modules.
TELEMETRY_DIR = telemetry_dir()
TELEMETRY_FILE = telemetry_file()


def generate_session_id() -> str:
    """Create a short anonymous session id."""
    return uuid.uuid4().hex[:12]


def log_event(
    session_id: str,
    event: str,
    platform: str = "unknown",
    step_index: int | None = None,
    duration_ms: float | None = None,
    success: bool | None = None,
    trap_type: str | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one telemetry event as a JSONL record."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "sid": session_id,
        "evt": event,
        "plat": platform,
        "step": step_index,
        "dur_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "ok": success,
        "trap": trap_type,
        "err": error_code,
        "meta": metadata or {},
    }
    with telemetry_file().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_events() -> list[dict[str, Any]]:
    """Read all telemetry events from disk."""
    path = telemetry_file()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(json.loads(line))
    return events


def get_summary() -> dict[str, Any]:
    """Aggregate telemetry into a compact operational summary."""
    events = read_events()
    if not events:
        return {"status": "empty", "msg": "No telemetry data recorded yet."}

    total_steps = sum(1 for event in events if event["evt"] == "step_end")
    successful_steps = sum(
        1 for event in events if event["evt"] == "step_end" and event.get("ok") is True
    )
    trap_hits = sum(1 for event in events if event.get("trap"))
    errors = sum(1 for event in events if event.get("err"))
    durations = [event["dur_ms"] for event in events if event.get("dur_ms") is not None]
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    return {
        "status": "ok",
        "total_events": len(events),
        "steps_completed": total_steps,
        "success_rate": round((successful_steps / total_steps * 100) if total_steps else 0.0, 1),
        "avg_step_time_ms": round(avg_duration, 1),
        "trap_hits": trap_hits,
        "errors": errors,
        "telemetry_file": str(telemetry_file()),
    }


def clear_telemetry() -> dict[str, str]:
    """Delete the telemetry stream for privacy resets or tests."""
    path = telemetry_file()
    if path.exists():
        path.unlink()
        return {"status": "cleared", "file": str(path)}
    return {"status": "nothing_to_clear"}
