"""Worker orchestration loop.

High-level coroutine that wires together the legacy :mod:`heypiggy_vision_worker`
entry coroutine with:

* :class:`worker.shutdown.ShutdownController` for graceful SIGTERM handling.
* :class:`worker.audit.AuditLogger` for per-run JSONL audit trail.
* :class:`worker.context.WorkerContext` as the DI container for config,
  circuit breaker and bridge state.

Rewriting the 3.5 k-line monolith line-by-line is out of scope for this
package — instead we expose :func:`run_worker` as a stable seam that tests
and the CLI can drive, and that forwards into the legacy coroutine with the
new telemetry wrapping around it.

Callers (tests + CLI) should always go through :func:`run_worker` so future
refactors stay invisible from outside.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from worker.audit import AuditLogger
from worker.exceptions import (
    PreflightError,
    ShutdownRequested,
    WorkerError,
)
from worker.logging import get_logger
from worker.shutdown import ShutdownController
from worker.telemetry import trace_span

if TYPE_CHECKING:
    from worker.context import WorkerContext


_log = get_logger(__name__)


async def run_worker(ctx: WorkerContext, *, dry_run: bool = False) -> None:
    """Drive one full worker run to completion.

    Args:
        ctx: Fully initialised :class:`WorkerContext`. Must already be the
            active context (entered via ``with ctx``).
        dry_run: If ``True``, perform config + preflight validation only
            and return without touching the bridge. Used by CI smoke tests
            and by ``heypiggy-worker run --dry-run``.

    Raises:
        PreflightError: If preflight validation fails.
        WorkerError: For any fatal domain-level error.
        ShutdownRequested: When a shutdown signal is received; callers
            should treat this as a clean exit.
    """
    audit = AuditLogger(ctx.artifacts.audit_log)
    audit.emit(
        "worker_run_started",
        run_id=ctx.artifacts.run_id,
        dry_run=dry_run,
        vision_model=ctx.config.vision.model,
        nvidia_primary=ctx.config.nvidia.primary_model,
    )

    async with ShutdownController() as shutdown:
        async with trace_span("worker.preflight", run_id=ctx.artifacts.run_id):
            _run_preflight(ctx)

        if dry_run:
            _log.info("worker_dry_run_ok", run_id=ctx.artifacts.run_id)
            audit.emit("worker_run_dry_run_ok", run_id=ctx.artifacts.run_id)
            return

        if shutdown.requested:
            audit.emit("worker_run_aborted", reason="shutdown_before_start")
            raise ShutdownRequested(shutdown.reason or "pre-start-signal")

        async with trace_span("worker.main_loop", run_id=ctx.artifacts.run_id):
            try:
                await _drive_legacy_loop(ctx, shutdown)
            except ShutdownRequested:
                audit.emit(
                    "worker_run_shutdown",
                    reason=shutdown.reason or "cooperative",
                )
                raise
            except WorkerError as exc:
                audit.emit(
                    "worker_run_failed",
                    error=type(exc).__name__,
                    error_message=str(exc),
                )
                raise
            except Exception as exc:
                audit.emit(
                    "worker_run_crashed",
                    error=type(exc).__name__,
                    error_message=str(exc),
                )
                raise WorkerError(
                    "unexpected error in worker loop",
                    cause=type(exc).__name__,
                ) from exc

    audit.emit("worker_run_completed", run_id=ctx.artifacts.run_id)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _run_preflight(ctx: WorkerContext) -> None:
    """Cheap sanity checks before the event loop enters bridge territory."""
    cfg = ctx.config

    if not cfg.nvidia.api_key:
        raise PreflightError(
            "NVIDIA_API_KEY is empty. Copy .env.example to .env and fill it in.",
            hint="set NVIDIA_API_KEY in the environment",
        )

    if cfg.vision.max_steps < 1:
        raise PreflightError(
            "MAX_STEPS must be >= 1",
            got=cfg.vision.max_steps,
        )
    if cfg.vision.max_retries < 0:
        raise PreflightError(
            "MAX_RETRIES must be >= 0",
            got=cfg.vision.max_retries,
        )

    ctx.artifacts.ensure_dirs()

    _log.info(
        "preflight_ok",
        run_id=ctx.artifacts.run_id,
        max_steps=cfg.vision.max_steps,
        max_retries=cfg.vision.max_retries,
        artifact_dir=str(ctx.artifacts.artifact_dir),
    )


# ---------------------------------------------------------------------------
# Legacy bridge
# ---------------------------------------------------------------------------


async def _drive_legacy_loop(
    ctx: WorkerContext,
    shutdown: ShutdownController,
) -> None:
    """Forward to the legacy ``heypiggy_vision_worker.main`` coroutine.

    The legacy module is intentionally untyped and out-of-scope for the
    strict ``worker.*`` contract — we import it lazily so the package
    remains usable in unit tests without the full bridge stack present.
    """
    try:
        from heypiggy_vision_worker import (
            main as _legacy_main,  # type: ignore[import-not-found,unused-ignore]
        )
    except ImportError as exc:
        raise WorkerError(
            "legacy heypiggy_vision_worker module is not importable",
            cause=str(exc),
        ) from exc

    # Poll the shutdown event on a cadence the legacy loop cannot cooperate
    # with; this is a best-effort wrapper until the legacy loop is fully
    # migrated into worker/*.
    if shutdown.requested:
        raise ShutdownRequested(shutdown.reason or "pre-call-signal")

    await _legacy_main()  # type: ignore[no-untyped-call]


__all__ = ["run_worker"]
