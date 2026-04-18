"""Unit tests for :mod:`worker.context`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from worker.context import (
    ActionState,
    ArtifactPaths,
    BridgeState,
    VisionState,
    WorkerContext,
    current_context,
)
from worker.exceptions import ConfigurationError


class TestBridgeState:
    def test_next_request_id_is_monotonic(self) -> None:
        s = BridgeState()
        assert s.next_request_id() == 1
        assert s.next_request_id() == 2
        assert s.next_request_id() == 3

    def test_defaults(self) -> None:
        s = BridgeState()
        assert s.tab_id is None
        assert s.window_id is None


class TestActionState:
    def test_reset_clears_counters(self) -> None:
        s = ActionState(captcha_attempts=5, click_escalation_step=3)
        s.reset()
        assert s.captcha_attempts == 0
        assert s.click_escalation_step == 0


class TestVisionState:
    def test_cache_clear(self) -> None:
        v = VisionState(circuit_breaker=MagicMock())
        v.cache[("a", "b")] = {"x": 1}
        v.cache_clear()
        assert v.cache == {}


class TestArtifactPaths:
    def test_audit_log_property(self, tmp_path: Path) -> None:
        p = ArtifactPaths(
            run_id="r1",
            artifact_dir=tmp_path,
            screenshot_dir=tmp_path / "s",
            audit_dir=tmp_path / "a",
            session_dir=tmp_path / "sess",
        )
        assert p.audit_log == tmp_path / "a" / "audit.jsonl"

    def test_ensure_dirs_creates_all(self, tmp_path: Path) -> None:
        p = ArtifactPaths(
            run_id="r1",
            artifact_dir=tmp_path / "art",
            screenshot_dir=tmp_path / "s",
            audit_dir=tmp_path / "a",
            session_dir=tmp_path / "sess",
        )
        p.ensure_dirs()
        for d in (p.artifact_dir, p.screenshot_dir, p.audit_dir, p.session_dir):
            assert d.is_dir()


class TestCurrentContextHelper:
    def test_raises_when_no_active_context(self) -> None:
        with pytest.raises(ConfigurationError, match="No active WorkerContext"):
            current_context()


class TestWorkerContextLifecycle:
    def _make_ctx(self, tmp_path: Path) -> WorkerContext:
        return WorkerContext(
            config=MagicMock(),
            artifacts=ArtifactPaths(
                run_id="r-test",
                artifact_dir=tmp_path,
                screenshot_dir=tmp_path / "s",
                audit_dir=tmp_path / "a",
                session_dir=tmp_path / "sess",
            ),
            vision=VisionState(circuit_breaker=MagicMock()),
        )

    def test_context_manager_binds_and_unbinds(self, tmp_path: Path) -> None:
        ctx = self._make_ctx(tmp_path)
        with ctx as active:
            assert current_context() is active
        with pytest.raises(ConfigurationError):
            current_context()

    def test_bind_tab_mirrors_into_logging(self, tmp_path: Path) -> None:
        from worker import logging as wlog

        ctx = self._make_ctx(tmp_path)
        ctx.bind_tab(tab_id=17, window_id=3)
        assert ctx.bridge.tab_id == 17
        assert ctx.bridge.window_id == 3
        # Clean up contextvar to avoid bleeding into other tests.
        wlog.set_tab_id(None)

    def test_reset_per_step_state(self, tmp_path: Path) -> None:
        ctx = self._make_ctx(tmp_path)
        ctx.actions.captcha_attempts = 4
        ctx.vision.cache[("a", "b")] = {}
        ctx.reset_per_step_state()
        assert ctx.actions.captcha_attempts == 0
        assert ctx.vision.cache == {}

    def test_nested_contexts_restore_correctly(self, tmp_path: Path) -> None:
        outer = self._make_ctx(tmp_path)
        inner = self._make_ctx(tmp_path)
        with outer:
            assert current_context() is outer
            with inner:
                assert current_context() is inner
            assert current_context() is outer
