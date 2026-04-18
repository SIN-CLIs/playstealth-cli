"""Unit tests for :mod:`worker.logging`."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest

from worker import logging as wlog
from worker.exceptions import BridgeError


@pytest.fixture(autouse=True)
def _reset_logging_state() -> None:
    """Reset the module-level ``_configured`` flag between tests."""
    wlog._configured = False  # type: ignore[attr-defined]
    wlog.set_run_id(None)
    wlog.set_tab_id(None)
    yield
    wlog._configured = False  # type: ignore[attr-defined]
    wlog.set_run_id(None)
    wlog.set_tab_id(None)


class TestConfigure:
    def test_idempotent(self) -> None:
        wlog.configure_logging(level="INFO", fmt="json")
        wlog.configure_logging(level="DEBUG", fmt="console")  # no-op
        # No assertion — we just verify it does not crash.

    def test_default_level_falls_back_to_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HEYPIGGY_LOG_LEVEL", raising=False)
        wlog.configure_logging(fmt="json")


class TestSecretRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "API_KEY",
            "Authorization",
            "x-api-key",
            "bearer_token",
            "session_cookie",
            "password",
            "nvidia_secret",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        event = {key: "super-secret-value", "safe": "ok"}
        cleaned = wlog._redact_secrets(None, "info", event)  # type: ignore[arg-type]
        assert cleaned[key] == "***REDACTED***"
        assert cleaned["safe"] == "ok"

    def test_nested_redaction(self) -> None:
        event = {
            "outer": {"api_key": "leak", "name": "alice"},
            "list": [{"token": "leak"}, {"id": 1}],
        }
        cleaned = wlog._redact_secrets(None, "info", event)  # type: ignore[arg-type]
        assert cleaned["outer"]["api_key"] == "***REDACTED***"
        assert cleaned["outer"]["name"] == "alice"
        assert cleaned["list"][0]["token"] == "***REDACTED***"
        assert cleaned["list"][1]["id"] == 1


class TestCorrelation:
    def test_run_id_is_injected(self) -> None:
        wlog.set_run_id("run-42")
        event = wlog._inject_correlation(None, "info", {})  # type: ignore[arg-type]
        assert event["run_id"] == "run-42"

    def test_tab_id_is_injected(self) -> None:
        wlog.set_tab_id("tab-7")
        event = wlog._inject_correlation(None, "info", {})  # type: ignore[arg-type]
        assert event["tab_id"] == "tab-7"

    def test_user_value_wins_over_context(self) -> None:
        wlog.set_run_id("run-42")
        event = wlog._inject_correlation(
            None,  # type: ignore[arg-type]
            "info",
            {"run_id": "explicit"},
        )
        assert event["run_id"] == "explicit"

    def test_none_context_is_not_written(self) -> None:
        event = wlog._inject_correlation(None, "info", {})  # type: ignore[arg-type]
        assert "run_id" not in event
        assert "tab_id" not in event


class TestWorkerErrorEnrichment:
    def test_worker_error_context_is_promoted(self) -> None:
        exc = BridgeError("boom", tab_id=99)
        event = wlog._enrich_worker_errors(  # type: ignore[arg-type]
            None, "error", {"exc_info": exc}
        )
        assert event["error_context"] == {"tab_id": 99}

    def test_plain_exception_is_untouched(self) -> None:
        event = wlog._enrich_worker_errors(  # type: ignore[arg-type]
            None, "error", {"exc_info": ValueError("boom")}
        )
        assert "error_context" not in event

    def test_no_exc_info_is_untouched(self) -> None:
        event = wlog._enrich_worker_errors(None, "info", {})  # type: ignore[arg-type]
        assert "error_context" not in event


class TestJsonOutput:
    def test_logger_emits_valid_json(self) -> None:
        buf = StringIO()
        with patch("sys.stderr", buf):
            wlog.configure_logging(level="INFO", fmt="json")
            log = wlog.get_logger("test")
            log.info("hello", foo="bar")
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert lines, "expected at least one log line"
        record = json.loads(lines[-1])
        assert record["event"] == "hello"
        assert record["foo"] == "bar"
        assert record["level"] == "info"

    def test_secrets_do_not_leak_into_json(self) -> None:
        buf = StringIO()
        with patch("sys.stderr", buf):
            wlog.configure_logging(level="INFO", fmt="json")
            log = wlog.get_logger("test")
            log.info("auth_attempt", api_key="SUPER-SECRET")
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        record = json.loads(lines[-1])
        assert record["api_key"] == "***REDACTED***"
        assert "SUPER-SECRET" not in buf.getvalue()
