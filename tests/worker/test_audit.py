"""Unit tests for :mod:`worker.audit`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker import logging as wlog
from worker.audit import AuditLogger


@pytest.fixture
def audit_log(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "audit.jsonl")


class TestAuditLogger:
    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "dir" / "audit.jsonl"
        AuditLogger(target)
        assert target.parent.exists()

    def test_writes_single_line(self, audit_log: AuditLogger) -> None:
        audit_log.emit("login", user="alice", ok=True)
        lines = audit_log.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "login"
        assert record["payload"] == {"user": "alice", "ok": True}
        assert record["v"] == "1"
        assert "ts" in record

    def test_appends_multiple(self, audit_log: AuditLogger) -> None:
        for i in range(3):
            audit_log.emit("tick", n=i)
        lines = audit_log.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert [json.loads(ln)["payload"]["n"] for ln in lines] == [0, 1, 2]

    def test_correlates_run_id(self, audit_log: AuditLogger) -> None:
        wlog.set_run_id("run-777")
        try:
            audit_log.emit("x")
        finally:
            wlog.set_run_id(None)
        record = json.loads(audit_log.path.read_text(encoding="utf-8").splitlines()[-1])
        assert record["run_id"] == "run-777"

    def test_path_encoding(self, audit_log: AuditLogger, tmp_path: Path) -> None:
        audit_log.emit("file", path=tmp_path / "x.png")
        record = json.loads(audit_log.path.read_text(encoding="utf-8").splitlines()[-1])
        assert record["payload"]["path"] == str(tmp_path / "x.png")

    def test_non_serializable_does_not_raise(self, audit_log: AuditLogger) -> None:
        class Weird:
            def __repr__(self) -> str:
                return "<weird>"

        audit_log.emit("weird", obj=Weird())
        record = json.loads(audit_log.path.read_text(encoding="utf-8").splitlines()[-1])
        assert record["payload"]["obj"] == "<weird>"

    def test_osError_is_swallowed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log = AuditLogger(tmp_path / "a.jsonl")

        def broken_open(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "open", broken_open)
        # Must not raise.
        log.emit("x")
