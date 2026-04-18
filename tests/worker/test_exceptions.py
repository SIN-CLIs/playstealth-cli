"""Unit tests for :mod:`worker.exceptions`."""

from __future__ import annotations

import pytest

from worker.exceptions import (
    ActionError,
    BridgeError,
    BridgeTimeoutError,
    ConfigurationError,
    ElementNotFoundError,
    PreflightError,
    ShutdownRequested,
    VisionCircuitOpenError,
    VisionError,
    WorkerError,
)


class TestHierarchy:
    def test_all_inherit_from_worker_error(self) -> None:
        for cls in (
            ConfigurationError,
            PreflightError,
            BridgeError,
            VisionError,
            ActionError,
            ShutdownRequested,
        ):
            assert issubclass(cls, WorkerError)

    def test_bridge_timeout_is_bridge_error(self) -> None:
        assert issubclass(BridgeTimeoutError, BridgeError)

    def test_vision_circuit_open_is_vision_error(self) -> None:
        assert issubclass(VisionCircuitOpenError, VisionError)

    def test_element_not_found_is_action_error(self) -> None:
        assert issubclass(ElementNotFoundError, ActionError)


class TestContextAttachment:
    def test_context_is_preserved(self) -> None:
        exc = BridgeError("boom", tab_id=42, method="call_tool")
        assert exc.context == {"tab_id": 42, "method": "call_tool"}

    def test_context_renders_in_str(self) -> None:
        exc = BridgeError("boom", tab_id=42)
        rendered = str(exc)
        assert "boom" in rendered
        assert "tab_id=42" in rendered

    def test_no_context_renders_clean(self) -> None:
        assert str(BridgeError("boom")) == "boom"

    def test_context_is_empty_dict_when_omitted(self) -> None:
        assert BridgeError("x").context == {}


class TestRaiseAndCatch:
    def test_can_raise_and_narrow_catch(self) -> None:
        with pytest.raises(BridgeError):
            raise BridgeTimeoutError("no response", timeout_s=5)

    def test_can_catch_at_worker_error(self) -> None:
        with pytest.raises(WorkerError):
            raise VisionError("bad")

    def test_shutdown_is_worker_error_but_conceptually_normal(self) -> None:
        with pytest.raises(ShutdownRequested) as info:
            raise ShutdownRequested("SIGTERM")
        assert "SIGTERM" in str(info.value)
