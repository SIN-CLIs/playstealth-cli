"""Tests for ``worker.cli``."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from worker import __version__
from worker.cli import main


def test_version_subcommand_prints_version() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["version"])
    assert rc == 0
    assert __version__ in buf.getvalue()


def test_version_flag_exits_cleanly() -> None:
    """``--version`` exits via SystemExit (argparse convention)."""
    with pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0


def test_doctor_reports_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NVIDIA_API_KEY", "HEYPIGGY_EMAIL", "HEYPIGGY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(["doctor"])

    assert rc == 2, "missing env must surface as exit code 2"
    combined = out.getvalue() + err.getvalue()
    assert "NVIDIA_API_KEY" in combined


def test_doctor_ok_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    monkeypatch.setenv("HEYPIGGY_EMAIL", "x@example.com")
    monkeypatch.setenv("HEYPIGGY_PASSWORD", "x")

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rc = main(["doctor"])

    assert rc == 0


def test_unknown_subcommand_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as ei:
        main(["no-such-command"])
    assert ei.value.code != 0


def test_help_does_not_crash() -> None:
    with pytest.raises(SystemExit) as ei, redirect_stdout(io.StringIO()):
        main(["--help"])
    assert ei.value.code == 0


def test_run_dry_run_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`heypiggy-worker run --dry-run` must exit 0 when env is valid."""
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rc = main(["run", "--dry-run"])

    assert rc == 0


def test_run_missing_api_key_exits_preflight_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing NVIDIA_API_KEY must surface as exit code 3 (preflight)."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rc = main(["run", "--dry-run"])

    assert rc == 3


def test_run_with_run_id_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("HEYPIGGY_ARTIFACT_BASE", str(tmp_path))

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rc = main(["run", "--run-id", "custom-123", "--dry-run"])

    assert rc == 0


def test_sync_envs_requires_infisical_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFISICAL_TOKEN", raising=False)
    monkeypatch.delenv("INFISICAL_SERVICE_TOKEN", raising=False)

    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(["sync-envs"])

    assert rc == 2
    assert "INFISICAL_TOKEN" in (out.getvalue() + err.getvalue())


def test_sync_envs_invokes_infisical_sync(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INFISICAL_TOKEN", "token-123")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")

    calls = {}

    def fake_sync_roots(roots, **kwargs):
        calls["roots"] = [str(root) for root in roots]
        calls.update(kwargs)
        return [object()]

    monkeypatch.setattr("infisical_sync.sync_roots", fake_sync_roots)

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rc = main(["sync-envs", "--root", str(tmp_path), "--env", "dev", "--path", "/opensin/test"])

    assert rc == 0
    assert calls["roots"] == [str(tmp_path)]
    assert calls["project_id"] == "proj-123"
    assert calls["environment"] == "dev"
    assert calls["folder_root"] == "/opensin/test"
