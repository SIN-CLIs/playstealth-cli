# ================================================================================
# DATEI: cli.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Command-line entry point for the HeyPiggy Vision Worker.

Thin argument-parsing shell around :func:`worker.loop.run_worker`. All real
logic lives in :mod:`worker.loop` so the CLI stays test-friendly.

Usage::

    heypiggy-worker --help
    heypiggy-worker run --log-format console
    heypiggy-worker version
    heypiggy-worker doctor
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from worker._version import __version__
from worker.exceptions import (
    ConfigurationError,
    PreflightError,
    ShutdownRequested,
    WorkerError,
)
from worker.logging import configure_logging, get_logger, set_run_id

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

_EXIT_OK: Final[int] = 0
_EXIT_CONFIG_ERROR: Final[int] = 2
_EXIT_PREFLIGHT_ERROR: Final[int] = 3
_EXIT_WORKER_ERROR: Final[int] = 4
_EXIT_INTERRUPTED: Final[int] = 130  # 128 + SIGINT


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heypiggy-worker",
        description=(
            "Autonomous HeyPiggy survey worker — vision-guided browser "
            "automation with self-healing fail-learning."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default=os.getenv("HEYPIGGY_LOG_LEVEL", "INFO"),
        help="Minimum log level (default: INFO or $HEYPIGGY_LOG_LEVEL).",
    )
    parser.add_argument(
        "--log-format",
        choices=("json", "console"),
        default=os.getenv("HEYPIGGY_LOG_FORMAT", "json"),
        help="Log output format (default: json or $HEYPIGGY_LOG_FORMAT).",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # --- run ---------------------------------------------------------------
    run = sub.add_parser("run", help="Start the vision-gated worker loop (default).")
    run.add_argument(
        "--run-id",
        default=None,
        help="Override the auto-generated run id (default: UTC timestamp).",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Initialise config + preflight only, then exit without driving the bridge.",
    )

    # --- doctor ------------------------------------------------------------
    sub.add_parser(
        "doctor",
        help="Inspect the environment and print a health report (no side effects).",
    )

    # --- version -----------------------------------------------------------
    sub.add_parser("version", help="Print the package version and exit.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (``sys.argv[1:]`` if ``None``).

    Returns:
        A process exit code. See module docstring for the meaning of each
        code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_logging(level=args.log_level, fmt=args.log_format)
    log = get_logger("worker.cli")

    command = args.command or "run"

    if command == "version":
        print(__version__)
        return _EXIT_OK

    if command == "doctor":
        return _run_doctor(log)

    if command == "run":
        return _run_worker(args, log)

    # argparse's parser.error() raises SystemExit internally but is typed as
    # returning None; calling sys.exit keeps mypy honest without a type-ignore.
    parser.error(f"unknown command: {command}")
    sys.exit(_EXIT_CONFIG_ERROR)  # pragma: no cover — unreachable


def _run_worker(args: argparse.Namespace, log: BoundLogger) -> int:
    """Wire up the async worker loop and translate exceptions into exit codes."""
    # Imports deferred so `--version` / `doctor` never pay the cost.
    from config import load_config_from_env  # legacy module
    from worker.context import WorkerContext
    from worker.loop import run_worker

    if args.run_id:
        os.environ["HEYPIGGY_RUN_ID"] = args.run_id

    try:
        cfg = load_config_from_env()
    except Exception as exc:  # pragma: no cover — config loader is resilient
        log.exception(
            "config_load_failed",
            error=type(exc).__name__,
            error_message=str(exc),
        )
        return _EXIT_CONFIG_ERROR

    set_run_id(cfg.artifacts.run_id)

    with WorkerContext.from_config(cfg) as ctx:
        log.info(
            "worker_starting",
            version=__version__,
            run_id=cfg.artifacts.run_id,
            dry_run=args.dry_run,
        )
        try:
            asyncio.run(run_worker(ctx, dry_run=args.dry_run))
        except ShutdownRequested as exc:
            log.info("worker_shutdown_clean", reason=str(exc) or "signal")
            return _EXIT_OK
        except ConfigurationError as exc:
            log.exception(
                "worker_config_error",
                error=type(exc).__name__,
                error_message=str(exc),
            )
            return _EXIT_CONFIG_ERROR
        except PreflightError as exc:
            log.exception(
                "worker_preflight_error",
                error=type(exc).__name__,
                error_message=str(exc),
            )
            return _EXIT_PREFLIGHT_ERROR
        except WorkerError as exc:
            log.exception(
                "worker_error",
                error=type(exc).__name__,
                error_message=str(exc),
            )
            return _EXIT_WORKER_ERROR
        except KeyboardInterrupt:
            log.warning("worker_interrupted")
            return _EXIT_INTERRUPTED
    return _EXIT_OK


def _run_doctor(log: BoundLogger) -> int:
    """Print a sanity report: version, required env vars, optional deps."""
    required_env = ("NVIDIA_API_KEY", "HEYPIGGY_EMAIL", "HEYPIGGY_PASSWORD")
    missing = [name for name in required_env if not os.environ.get(name)]

    # Optional deps
    def _probe(module_name: str) -> str:
        try:
            __import__(module_name)
        except ImportError as exc:
            return f"missing ({exc.msg})"
        return "ok"

    modules: dict[str, str] = {
        "PIL": _probe("PIL"),
        "structlog": _probe("structlog"),
        "opentelemetry": _probe("opentelemetry"),
    }
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "missing_env": missing,
        "modules": modules,
    }

    log.info("doctor_report", **report)

    # Human-readable summary on stdout too (stderr has the structured log).
    print(f"heypiggy-worker {__version__} on {sys.platform} / Python {sys.version.split()[0]}")
    if missing:
        print(f"  missing required env: {', '.join(missing)}")
    else:
        print("  required env: ok")
    for mod, status in modules.items():
        print(f"  {mod}: {status}")

    return _EXIT_CONFIG_ERROR if missing else _EXIT_OK


__all__ = ["main"]
