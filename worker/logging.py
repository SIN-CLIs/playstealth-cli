"""Structured logging for the HeyPiggy Vision Worker.

Uses :mod:`structlog` for JSON-by-default logs with:

* **Run-ID correlation** — every log line carries the current run/tab id
  via :class:`~contextvars.ContextVar`, so you can ``grep`` by run.
* **Secret redaction** — keys matching the DENY_KEYS set are scrubbed
  before serialization so no API keys leak into logs.
* **Exception enrichment** — :class:`~worker.exceptions.WorkerError`
  ``.context`` fields are merged into the log record.

All worker code should do:

.. code-block:: python

    from worker.logging import get_logger
    log = get_logger(__name__)
    log.info("submitted_answer", question_id=qid, choice=choice)

Never use ``print()`` in worker code. Ruff rule ``T20`` enforces this.
"""

from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final, Literal, cast

import structlog
from structlog.typing import EventDict, Processor

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

# ---------------------------------------------------------------------------
# Correlation context
# ---------------------------------------------------------------------------

_run_id_var: ContextVar[str | None] = ContextVar("heypiggy_run_id", default=None)
_tab_id_var: ContextVar[str | None] = ContextVar("heypiggy_tab_id", default=None)


def set_run_id(run_id: str | None) -> None:
    """Attach ``run_id`` to every subsequent log record in this context."""
    _run_id_var.set(run_id)


def set_tab_id(tab_id: str | None) -> None:
    """Attach ``tab_id`` to every subsequent log record in this context."""
    _tab_id_var.set(tab_id)


def get_run_id() -> str | None:
    """Return the current run id, if any."""
    return _run_id_var.get()


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

#: Keys whose *values* are fully redacted. Matching is case-insensitive and
#: substring-based so ``NVIDIA_API_KEY``, ``api_key``, ``X-API-Key`` all hit.
DENY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "bearer",
        "cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "session",
        "x-api-key",
    }
)

_REDACTED: Final[str] = "***REDACTED***"


def _redact_secrets(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Redact values whose key matches :data:`DENY_KEYS` (recursively)."""
    redacted = _walk_and_redact(event_dict)
    return cast("EventDict", redacted)


def _walk_and_redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        redacted: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_secret_key(k):
                redacted[k] = _REDACTED
            else:
                redacted[k] = _walk_and_redact(v)
        return redacted
    if isinstance(obj, list):
        return [_walk_and_redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_walk_and_redact(v) for v in obj)
    return obj


def _is_secret_key(key: str) -> bool:
    low = key.lower()
    return any(deny in low for deny in DENY_KEYS)


# ---------------------------------------------------------------------------
# Context-var injection
# ---------------------------------------------------------------------------


def _inject_correlation(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Merge run-id / tab-id context vars into every record."""
    if (run_id := _run_id_var.get()) is not None:
        event_dict.setdefault("run_id", run_id)
    if (tab_id := _tab_id_var.get()) is not None:
        event_dict.setdefault("tab_id", tab_id)
    return event_dict


def _enrich_worker_errors(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Promote :class:`WorkerError.context` into the record."""
    exc_info = event_dict.get("exc_info")
    if exc_info is None:
        return event_dict
    from worker.exceptions import WorkerError  # local import to avoid cycles

    exc: BaseException | None = None
    if isinstance(exc_info, BaseException):
        exc = exc_info
    elif isinstance(exc_info, tuple) and len(exc_info) >= 2:
        exc = exc_info[1] if isinstance(exc_info[1], BaseException) else None
    if isinstance(exc, WorkerError) and exc.context:
        event_dict.setdefault("error_context", exc.context)
    return event_dict


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]

_configured: bool = False


def configure_logging(
    *,
    level: LogLevel | str | None = None,
    fmt: LogFormat | str | None = None,
    utc: bool = True,
) -> None:
    """Configure structlog + stdlib ``logging`` interop.

    Idempotent: safe to call multiple times, but only the first call has an
    effect.

    Args:
        level: Minimum log level. Defaults to ``HEYPIGGY_LOG_LEVEL`` or INFO.
        fmt: ``"json"`` (production) or ``"console"`` (human-readable).
            Defaults to ``HEYPIGGY_LOG_FORMAT`` or ``"json"``.
        utc: Use UTC timestamps (default) vs local time.
    """
    global _configured  # noqa: PLW0603 — module-level singleton flag
    if _configured:
        return

    resolved_level = (level or os.getenv("HEYPIGGY_LOG_LEVEL") or "INFO").upper()
    resolved_fmt = (fmt or os.getenv("HEYPIGGY_LOG_FORMAT") or "json").lower()

    numeric_level = logging.getLevelName(resolved_level)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    # stdlib baseline so third-party loggers funnel through structlog.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=numeric_level,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=utc),
        _inject_correlation,
        _enrich_worker_errors,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_secrets,
    ]

    renderer: Processor
    if resolved_fmt == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    else:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a bound structlog logger.

    Auto-configures on first use with defaults from the environment so
    library-style consumers don't have to remember to call
    :func:`configure_logging` first.
    """
    if not _configured:
        configure_logging()
    logger: BoundLogger = structlog.get_logger(name)
    return logger


__all__ = [
    "DENY_KEYS",
    "configure_logging",
    "get_logger",
    "get_run_id",
    "set_run_id",
    "set_tab_id",
]
