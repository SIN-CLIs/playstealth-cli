"""Ban-risk monitor derived from local telemetry.

The goal is not to predict platform internals perfectly. We simply score the
observable signals that correlate with risk: speed, disqualifications, and trap
hits.
"""

from __future__ import annotations

from playstealth_actions.telemetry import read_events


def calculate_ban_risk(session_id: str | None = None) -> dict[str, object]:
    """Calculate a simple risk score from telemetry events."""
    events = read_events()
    if session_id:
        events = [event for event in events if event.get("sid") == session_id]
    if not events:
        return {"risk": 0.0, "status": "no_data"}

    total_steps = sum(1 for event in events if event["evt"] == "step_end")
    disqualifications = sum(1 for event in events if event["evt"] == "disqualified")
    traps_hit = sum(1 for event in events if event.get("trap"))
    fast_steps = sum(
        1 for event in events if event["evt"] == "step_end" and (event.get("dur_ms") or 9999) < 3000
    )

    if total_steps == 0:
        return {"risk": 0.0, "status": "no_steps"}

    risk = (
        (disqualifications / total_steps) * 0.4
        + (fast_steps / total_steps) * 0.3
        + (traps_hit / total_steps) * 0.3
    ) * 100
    status = "safe" if risk < 15 else ("warning" if risk < 30 else "critical")
    return {
        "risk": round(risk, 1),
        "status": status,
        "disqualifications": disqualifications,
        "fast_steps": fast_steps,
        "traps": traps_hit,
    }
