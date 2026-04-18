"""Tests for ``worker.loop``.

These tests avoid importing the legacy monolith — they exercise only the
preflight + shutdown paths that ``worker.loop`` owns itself.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from config import load_config_from_env
from worker.context import WorkerContext
from worker.exceptions import PreflightError, ShutdownRequested
from worker.loop import _run_preflight, run_worker


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> WorkerContext:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))
    monkeypatch.setenv("HEYPIGGY_RUN_ID", "run-test")
    cfg = load_config_from_env()
    with WorkerContext.from_config(cfg) as ctx:
        yield ctx


def test_preflight_ok(ctx: WorkerContext) -> None:
    # Should not raise.
    _run_preflight(ctx)
    assert ctx.artifacts.artifact_dir.exists()
    assert ctx.artifacts.audit_dir.exists()


def test_preflight_missing_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))
    cfg = load_config_from_env()
    with WorkerContext.from_config(cfg) as ctx, pytest.raises(PreflightError):
        _run_preflight(ctx)


@pytest.mark.asyncio
async def test_dry_run_does_not_touch_bridge(ctx: WorkerContext) -> None:
    # Patch the legacy import so test fails loudly if dry_run ever calls it.
    with patch("worker.loop._drive_legacy_loop") as mock_drive:
        await run_worker(ctx, dry_run=True)
        mock_drive.assert_not_called()

    audit_path = ctx.artifacts.audit_log
    assert audit_path.exists()
    content = audit_path.read_text(encoding="utf-8")
    assert "worker_run_started" in content
    assert "worker_run_dry_run_ok" in content


@pytest.mark.asyncio
async def test_shutdown_before_start_raises(
    ctx: WorkerContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If shutdown fires during preflight, we must raise ShutdownRequested."""
    from worker import shutdown as shutdown_mod

    original_enter = shutdown_mod.ShutdownController.__aenter__

    async def _enter_and_request(self: shutdown_mod.ShutdownController):
        ctrl = await original_enter(self)
        ctrl.request(reason="test-pre-start")
        return ctrl

    monkeypatch.setattr(shutdown_mod.ShutdownController, "__aenter__", _enter_and_request)

    with pytest.raises(ShutdownRequested):
        await run_worker(ctx, dry_run=False)


def test_preflight_rejects_bad_max_steps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))
    monkeypatch.setenv("MAX_STEPS", "0")
    cfg = load_config_from_env()
    with WorkerContext.from_config(cfg) as c, pytest.raises(PreflightError):
        _run_preflight(c)


def test_preflight_rejects_negative_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))
    monkeypatch.setenv("MAX_RETRIES", "-1")
    cfg = load_config_from_env()
    with WorkerContext.from_config(cfg) as c, pytest.raises(PreflightError):
        _run_preflight(c)


@pytest.mark.asyncio
async def test_drive_legacy_loop_converts_import_error(
    ctx: WorkerContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing legacy module must raise WorkerError, not bare ImportError."""
    import sys

    from worker.exceptions import WorkerError
    from worker.loop import _drive_legacy_loop
    from worker.shutdown import ShutdownController

    # Setting to None makes Python raise ModuleNotFoundError on the next import.
    monkeypatch.setitem(sys.modules, "heypiggy_vision_worker", None)

    async with ShutdownController() as shutdown:
        with pytest.raises(WorkerError):
            await _drive_legacy_loop(ctx, shutdown)


@pytest.mark.asyncio
async def test_drive_legacy_loop_respects_shutdown(
    ctx: WorkerContext,
) -> None:
    from worker.loop import _drive_legacy_loop
    from worker.shutdown import ShutdownController

    async with ShutdownController() as shutdown:
        shutdown.request("pre-call-test")
        with pytest.raises(ShutdownRequested):
            await _drive_legacy_loop(ctx, shutdown)  # must abort before legacy import
