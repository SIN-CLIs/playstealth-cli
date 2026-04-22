from playstealth_actions.ban_risk_monitor import calculate_ban_risk
from playstealth_actions.telemetry import (
    clear_telemetry,
    generate_session_id,
    get_summary,
    log_event,
)


def test_telemetry_summary_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAYSTEALTH_STATE_DIR", str(tmp_path))
    clear_telemetry()
    session_id = generate_session_id()
    log_event(session_id, "step_end", step_index=1, duration_ms=4500, success=True)
    summary = get_summary()
    assert summary["status"] == "ok"
    assert summary["steps_completed"] == 1
    assert summary["success_rate"] == 100.0


def test_ban_risk_increases_with_bad_signals(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAYSTEALTH_STATE_DIR", str(tmp_path))
    clear_telemetry()
    session_id = generate_session_id()
    log_event(
        session_id,
        "step_end",
        step_index=1,
        duration_ms=1200,
        success=True,
        trap_type="attention_check",
    )
    log_event(session_id, "disqualified", step_index=2, success=False)
    risk = calculate_ban_risk(session_id)
    assert risk["risk"] > 0
    assert risk["status"] in {"warning", "critical"}
