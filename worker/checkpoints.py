# ================================================================================
# DATEI: checkpoints.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path


class IllegalTransitionError(RuntimeError):
    # ========================================================================
    # KLASSE: IllegalTransitionError(RuntimeError)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    pass


class AgentState(str, Enum):
    # ========================================================================
    # KLASSE: AgentState(str, Enum)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    INIT = "INIT"
    PREFLIGHT = "PREFLIGHT"
    OPEN_LOGIN = "OPEN_LOGIN"
    AUTHENTICATE = "AUTHENTICATE"
    OPEN_DASHBOARD = "OPEN_DASHBOARD"
    SELECT_SURVEY = "SELECT_SURVEY"
    EXECUTE_TASK_LOOP = "EXECUTE_TASK_LOOP"
    POST_ACTION_VERIFY = "POST_ACTION_VERIFY"
    CAPTCHA = "CAPTCHA"
    BRIDGE_RECOVERY = "BRIDGE_RECOVERY"
    SESSION_SAVE = "SESSION_SAVE"
    COMPLETE = "COMPLETE"
    FAIL_SAFE = "FAIL_SAFE"
    ESCALATE = "ESCALATE"


TRANSITION_TABLE: dict[AgentState, tuple[AgentState, ...]] = {
    AgentState.INIT: (AgentState.PREFLIGHT, AgentState.OPEN_LOGIN, AgentState.EXECUTE_TASK_LOOP),
    AgentState.PREFLIGHT: (
        AgentState.OPEN_LOGIN,
        AgentState.AUTHENTICATE,
        AgentState.EXECUTE_TASK_LOOP,
    ),
    AgentState.OPEN_LOGIN: (AgentState.AUTHENTICATE, AgentState.OPEN_DASHBOARD),
    AgentState.AUTHENTICATE: (AgentState.OPEN_DASHBOARD, AgentState.CAPTCHA),
    AgentState.OPEN_DASHBOARD: (AgentState.SELECT_SURVEY, AgentState.EXECUTE_TASK_LOOP),
    AgentState.SELECT_SURVEY: (AgentState.EXECUTE_TASK_LOOP, AgentState.CAPTCHA),
    AgentState.EXECUTE_TASK_LOOP: (
        AgentState.POST_ACTION_VERIFY,
        AgentState.CAPTCHA,
        AgentState.SESSION_SAVE,
        AgentState.COMPLETE,
    ),
    AgentState.POST_ACTION_VERIFY: (
        AgentState.EXECUTE_TASK_LOOP,
        AgentState.SELECT_SURVEY,
        AgentState.OPEN_DASHBOARD,
    ),
    AgentState.CAPTCHA: (AgentState.POST_ACTION_VERIFY, AgentState.BRIDGE_RECOVERY),
    AgentState.BRIDGE_RECOVERY: (AgentState.EXECUTE_TASK_LOOP, AgentState.OPEN_DASHBOARD),
    AgentState.SESSION_SAVE: (AgentState.EXECUTE_TASK_LOOP, AgentState.COMPLETE),
    AgentState.COMPLETE: (),
    AgentState.FAIL_SAFE: (),
    AgentState.ESCALATE: (),
}


@dataclass(slots=True)
class StepContext:
    # ========================================================================
    # KLASSE: StepContext
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    run_id: str
    state: AgentState
    step_index: int = 0
    max_steps: int = 120
    no_progress_counter: int = 0
    last_page_fingerprint: str = ""
    task_url: str | None = None
    earnings_so_far: float = 0.0
    saved_at: str = ""

    def __post_init__(self) -> None:
        self.state = _normalize_state(self.state)


@dataclass(slots=True)
class ArchivedRun:
    # ========================================================================
    # KLASSE: ArchivedRun
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    run_id: str
    earnings: float
    duration_seconds: float
    path: Path


def checkpoint_path(artifact_dir: Path) -> Path:
    return artifact_dir / "checkpoint.json"


def save_checkpoint(ctx: StepContext, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = replace(ctx, saved_at=_utc_now())
    payload = asdict(stamped)
    payload["state"] = stamped.state.value
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
    return path


def load_checkpoint(path: Path, *, max_age_seconds: int = 7200) -> StepContext | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    saved_at = str(payload.get("saved_at", ""))
    if not _is_fresh(saved_at, max_age_seconds=max_age_seconds):
        return None
    return StepContext(
        run_id=str(payload.get("run_id", "")),
        state=_normalize_state(payload.get("state", AgentState.INIT.value)),
        step_index=int(payload.get("step_index", 0)),
        max_steps=int(payload.get("max_steps", 120)),
        no_progress_counter=int(payload.get("no_progress_counter", 0)),
        last_page_fingerprint=str(payload.get("last_page_fingerprint", "")),
        task_url=str(payload.get("task_url", "")) or None,
        earnings_so_far=float(payload.get("earnings_so_far", 0.0) or 0.0),
        saved_at=saved_at,
    )


def find_latest_checkpoint(
    base_dir: Path, *, max_age_seconds: int = 7200
) -> tuple[Path, StepContext] | None:
    candidates = sorted(
        base_dir.glob("heypiggy_run_*/checkpoint.json"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        checkpoint = load_checkpoint(candidate, max_age_seconds=max_age_seconds)
        if checkpoint is not None:
            return candidate, checkpoint
    return None


def step_context_advance(
    ctx: StepContext,
    path: Path,
    *,
    state: AgentState | str,
    reason: str = "transition",
    step_index: int | None = None,
    task_url: str | None = None,
    no_progress_counter: int | None = None,
    last_page_fingerprint: str | None = None,
    earnings_so_far: float | None = None,
) -> StepContext:
    new_state = _normalize_state(state)
    if new_state not in (AgentState.FAIL_SAFE, AgentState.ESCALATE):
        allowed = TRANSITION_TABLE.get(ctx.state, ())
        if new_state not in allowed:
            raise IllegalTransitionError(
                f"illegal transition: {ctx.state.value} -> {new_state.value}"
            )
    updated = replace(
        ctx,
        state=new_state,
        step_index=ctx.step_index if step_index is None else step_index,
        max_steps=ctx.max_steps,
        task_url=ctx.task_url if task_url is None else task_url,
        no_progress_counter=(
            ctx.no_progress_counter if no_progress_counter is None else no_progress_counter
        ),
        last_page_fingerprint=(
            ctx.last_page_fingerprint if last_page_fingerprint is None else last_page_fingerprint
        ),
        earnings_so_far=ctx.earnings_so_far if earnings_so_far is None else earnings_so_far,
    )
    save_checkpoint(updated, path)
    _append_transition_log(
        path.with_name("state_transitions.jsonl"),
        old_state=ctx.state,
        new_state=new_state,
        reason=reason,
        step_index=updated.step_index,
        run_id=updated.run_id,
    )
    return updated


def clear_checkpoint(path: Path) -> None:
    path.unlink(missing_ok=True)


def archive_run_bundle(artifact_dir: Path, run_id: str, *, base_dir: Path) -> Path:
    archive_root = base_dir / "runs" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    target = archive_root / run_id
    if target.exists():
        target = archive_root / f"{run_id}-{datetime.now(tz=UTC).strftime('%H%M%S')}"
    shutil.move(str(artifact_dir), str(target))
    return target


def fail_safe(ctx: StepContext, checkpoint_file: Path, reason: str) -> int:
    step_context_advance(ctx, checkpoint_file, state=AgentState.FAIL_SAFE, reason=reason)
    _close_heypiggy_tabs()
    return 0


def escalate(
    ctx: StepContext,
    checkpoint_file: Path,
    artifact_dir: Path,
    reason: str,
    *,
    exception: BaseException | None = None,
) -> int:
    updated = step_context_advance(ctx, checkpoint_file, state=AgentState.ESCALATE, reason=reason)
    dump_path = artifact_dir / "escalation_dump.json"
    dump_path.write_text(
        json.dumps(
            {
                "run_id": updated.run_id,
                "state": updated.state.value,
                "reason": reason,
                "exception": repr(exception) if exception is not None else "",
                "saved_at": updated.saved_at,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _send_telegram_alert(updated.run_id, reason, exception)
    return 1


def list_recent_archives(base_dir: Path, *, limit: int = 5) -> list[ArchivedRun]:
    archive_root = base_dir / "runs" / "archive"
    if not archive_root.exists():
        return []
    candidates = sorted(
        [path for path in archive_root.iterdir() if path.is_dir()],
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )[:limit]
    runs: list[ArchivedRun] = []
    for candidate in candidates:
        summary_path = candidate / "run_summary.json"
        earnings = 0.0
        duration_seconds = 0.0
        if summary_path.exists():
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                earnings = float(payload.get("earnings", 0.0) or 0.0)
                duration_seconds = float(payload.get("duration_seconds", 0.0) or 0.0)
        runs.append(
            ArchivedRun(
                run_id=candidate.name,
                earnings=earnings,
                duration_seconds=duration_seconds,
                path=candidate,
            )
        )
    return runs


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _normalize_state(value: AgentState | str | object) -> AgentState:
    if isinstance(value, AgentState):
        return value
    text = str(value).strip().upper()
    return AgentState[text]


def _append_transition_log(
    path: Path,
    *,
    old_state: AgentState,
    new_state: AgentState,
    reason: str,
    step_index: int,
    run_id: str,
) -> None:
    record = {
        "ts": _utc_now(),
        "run_id": run_id,
        "old_state": old_state.value,
        "new_state": new_state.value,
        "reason": reason,
        "step_index": step_index,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _close_heypiggy_tabs() -> None:
    if shutil.which("osascript") is None:
        return
    script = (
        'tell application "Google Chrome"\n'
        "repeat with w in windows\n"
        "repeat with t in tabs of w\n"
        'if URL of t contains "heypiggy.com" then close t\n'
        "end repeat\n"
        "end repeat\n"
        "end tell"
    )
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)


def _send_telegram_alert(run_id: str, reason: str, exception: BaseException | None = None) -> None:
    token = os.environ.get("HEYPIGGY_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("HEYPIGGY_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    message = f"HeyPiggy ESCALATE\nrun_id={run_id}\nreason={reason}"
    if exception is not None:
        message += f"\nexception={type(exception).__name__}: {exception}"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except Exception:
        return


def _is_fresh(saved_at: str, *, max_age_seconds: int) -> bool:
    if not saved_at:
        return False
    try:
        timestamp = datetime.fromisoformat(saved_at)
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return datetime.now(tz=UTC) - timestamp <= timedelta(seconds=max_age_seconds)


__all__ = [
    "AgentState",
    "ArchivedRun",
    "IllegalTransitionError",
    "StepContext",
    "archive_run_bundle",
    "checkpoint_path",
    "clear_checkpoint",
    "escalate",
    "fail_safe",
    "find_latest_checkpoint",
    "list_recent_archives",
    "load_checkpoint",
    "save_checkpoint",
    "step_context_advance",
    "TRANSITION_TABLE",
]
