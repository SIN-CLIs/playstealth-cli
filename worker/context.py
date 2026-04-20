# ================================================================================
# DATEI: context.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Worker execution context — the DI container that replaced the globals.

The legacy worker carried 32 module-level globals and 13 ``global`` statements
scattered across 3579 lines of code. Every subsystem mutated them, which made
testing impossible and caused subtle race conditions across runs.

:class:`WorkerContext` now owns *all* per-run mutable state:

* Config snapshot (immutable — taken once at boot)
* Bridge identifiers (``tab_id``, ``window_id``)
* Vision subsystem (circuit breaker + response cache)
* Run tracking (``RunSummary``, per-step counters)
* Action-level counters (captcha attempts, click escalation step)

Deeply nested helpers that can't plausibly receive the context as an argument
look it up via :func:`current_context`, which reads from a
:class:`~contextvars.ContextVar`. This is the only escape hatch; regular
code paths should always pass the context explicitly.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from worker.exceptions import ConfigurationError

if TYPE_CHECKING:
    # Legacy modules imported lazily to keep the package importable without
    # them installed (useful for isolated unit tests).
    from circuit_breaker import CircuitBreaker
    from config import WorkerConfig
    from observability import RunSummary


# ---------------------------------------------------------------------------
# ContextVar-backed "current" context
# ---------------------------------------------------------------------------

_current_context: ContextVar[WorkerContext | None] = ContextVar(
    "heypiggy_worker_context", default=None
)


def current_context() -> WorkerContext:
    """Return the active :class:`WorkerContext`.

    Raises:
        ConfigurationError: If called outside an active context.
    """
    ctx = _current_context.get()
    if ctx is None:
        raise ConfigurationError("No active WorkerContext. Enter `with WorkerContext(...)` first.")
    return ctx


# ---------------------------------------------------------------------------
# Per-subsystem state holders
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BridgeState:
    # ========================================================================
    # KLASSE: BridgeState
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Mutable bridge identifiers, rebound on every tab/window switch."""

    tab_id: int | None = None
    window_id: int | None = None
    request_id_counter: int = 0

    def next_request_id(self) -> int:
        """Return a monotonically increasing request id."""
        self.request_id_counter += 1
        return self.request_id_counter


@dataclass(slots=True)
class VisionState:
    """Vision subsystem state — circuit breaker + per-run response cache."""

    circuit_breaker: CircuitBreaker
    cache: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)

    def cache_clear(self) -> None:
        """Wipe the cache (called between runs / tab navigations)."""
        self.cache.clear()


@dataclass(slots=True)
class ActionState:
    """Counters that the controller uses to escalate between action modes."""

    captcha_attempts: int = 0
    click_escalation_step: int = 0

    def reset(self) -> None:
        """Reset all counters to zero (called between control-loop steps)."""
        self.captcha_attempts = 0
        self.click_escalation_step = 0


@dataclass(slots=True)
class ArtifactPaths:
    """Resolved filesystem locations for run artifacts."""

    run_id: str
    artifact_dir: Path
    screenshot_dir: Path
    audit_dir: Path
    session_dir: Path

    @property
    def audit_log(self) -> Path:
        """Path to the append-only JSONL audit log for the current run."""
        return self.audit_dir / "audit.jsonl"

    def ensure_dirs(self) -> None:
        """Create all artifact directories (idempotent)."""
        for d in (self.artifact_dir, self.screenshot_dir, self.audit_dir, self.session_dir):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Top-level context
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WorkerContext:
    # ========================================================================
    # KLASSE: WorkerContext
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Everything a running worker needs to hand-off between subsystems.

    One instance exists per process. Enter it as a context manager so it
    binds itself into the :class:`~contextvars.ContextVar` stack::

        with WorkerContext.from_config(cfg) as ctx:
            await run_worker(ctx)
    """

    config: WorkerConfig
    artifacts: ArtifactPaths
    vision: VisionState
    bridge: BridgeState = field(default_factory=BridgeState)
    actions: ActionState = field(default_factory=ActionState)
    run_summary: RunSummary | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    """Free-form bag for caller-attached metadata. Do not abuse."""

    _token: Token[WorkerContext | None] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    # ----------------------------------------------------------- factories

    @classmethod
    def from_config(
        cls,
        config: WorkerConfig,
        *,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> Self:
        """Build a context from a loaded :class:`WorkerConfig`.

        Args:
            config: Frozen config snapshot.
            circuit_breaker: Inject a pre-built circuit breaker (used by
                tests). Production code passes ``None`` and gets the
                default thresholds from :mod:`circuit_breaker`.
        """
        from circuit_breaker import CircuitBreaker  # local: legacy module

        artifacts = ArtifactPaths(
            run_id=config.artifacts.run_id,
            artifact_dir=Path(config.artifacts.artifact_dir),
            screenshot_dir=Path(config.artifacts.screenshot_dir),
            audit_dir=Path(config.artifacts.audit_dir),
            session_dir=Path(config.artifacts.session_dir),
        )
        artifacts.ensure_dirs()

        cb = circuit_breaker or CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        vision = VisionState(circuit_breaker=cb)
        return cls(config=config, artifacts=artifacts, vision=vision)

    # -------------------------------------------------------- context mgr

    def __enter__(self) -> Self:
        self._token = _current_context.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _current_context.reset(self._token)
            self._token = None

    # ----------------------------------------------------------- helpers

    def bind_tab(self, *, tab_id: int | None, window_id: int | None) -> None:
        """Update bridge identifiers + mirror into the logging correlation."""
        from worker.logging import set_tab_id as _set_tab_id

        self.bridge.tab_id = tab_id
        self.bridge.window_id = window_id
        _set_tab_id(str(tab_id) if tab_id is not None else None)

    def reset_per_step_state(self) -> None:
        """Clear everything that should not leak between control-loop steps."""
        self.actions.reset()
        self.vision.cache_clear()


__all__ = [
    "ActionState",
    "ArtifactPaths",
    "BridgeState",
    "VisionState",
    "WorkerContext",
    "current_context",
]
