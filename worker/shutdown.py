"""Cooperative, signal-driven graceful shutdown.

The legacy worker had no signal handling — SIGTERM from Kubernetes killed
mid-action, corrupting the run summary. :class:`ShutdownController` fixes
that:

* Installs handlers for SIGINT + SIGTERM (and SIGHUP on POSIX).
* Exposes an :class:`asyncio.Event` long-running loops can ``await``.
* First signal → soft shutdown (finish current iteration then exit).
* Second signal of the same kind within 3 s → hard exit.

Usage::

    async def main() -> None:
        async with ShutdownController() as shutdown:
            while not shutdown.requested:
                await one_iteration()
                await shutdown.wait(timeout=1.0)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
from types import FrameType, TracebackType
from typing import Any, Final, Self

from worker.logging import get_logger

_log = get_logger(__name__)

_DOUBLE_TAP_WINDOW_S: Final[float] = 3.0

# Signals we install handlers for. SIGHUP is POSIX-only.
_HANDLED_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM) + (
    (signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ()
)


class ShutdownController:
    """Async context manager that installs graceful-shutdown signal handlers.

    Thread-safe across the single event-loop thread — not meant for
    multi-loop use.
    """

    __slots__ = ("_event", "_first_signal_ts", "_loop", "_original_handlers", "_reason")

    def __init__(self) -> None:
        self._event: asyncio.Event = asyncio.Event()
        self._first_signal_ts: dict[signal.Signals, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._original_handlers: dict[signal.Signals, Any] = {}
        self._reason: str | None = None

    # ------------------------------------------------------------------ API

    @property
    def requested(self) -> bool:
        """``True`` once a shutdown signal has been received."""
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        """Human-readable reason (e.g. ``"SIGTERM"``) or ``None``."""
        return self._reason

    async def wait(self, *, timeout: float | None = None) -> bool:
        """Wait up to ``timeout`` seconds for a shutdown signal.

        Returns:
            ``True`` if shutdown was requested, ``False`` on timeout.
        """
        if timeout is None:
            await self._event.wait()
            return True
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    def request(self, reason: str = "manual") -> None:
        """Trigger shutdown programmatically (useful for tests)."""
        if not self._event.is_set():
            self._reason = reason
            self._event.set()
            _log.info("shutdown_requested", reason=reason)

    # ------------------------------------------------------ context manager

    async def __aenter__(self) -> Self:
        self._loop = asyncio.get_running_loop()
        self._install_handlers()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._restore_handlers()

    # ----------------------------------------------------------- internals

    def _install_handlers(self) -> None:
        if self._loop is None:
            # Defensive: should be unreachable because __aenter__ sets it
            # first. Kept as a real error (not `assert`) so it survives
            # running under ``python -O``.
            raise RuntimeError("ShutdownController._install_handlers called before __aenter__")
        for sig in _HANDLED_SIGNALS:
            try:
                self._loop.add_signal_handler(sig, self._on_signal, sig)
            except NotImplementedError:
                # Windows / non-main thread — fall back to signal.signal().
                previous = signal.getsignal(sig)
                self._original_handlers[sig] = previous
                signal.signal(sig, self._on_signal_sync)

    def _restore_handlers(self) -> None:
        if self._loop is None:
            return  # nothing was ever installed
        for sig in _HANDLED_SIGNALS:
            with contextlib.suppress(NotImplementedError, ValueError):
                self._loop.remove_signal_handler(sig)
        for sig, previous in self._original_handlers.items():
            with contextlib.suppress(OSError, ValueError):  # pragma: no cover
                signal.signal(sig, previous)
        self._original_handlers.clear()

    def _on_signal(self, sig: signal.Signals) -> None:
        now = time.monotonic()
        first = self._first_signal_ts.get(sig)
        if first is not None and (now - first) <= _DOUBLE_TAP_WINDOW_S:
            _log.warning("shutdown_forced", signal=sig.name)
            # Best-effort flush of loggers, then hard-exit.
            os._exit(130)  # 128 + SIGINT
        self._first_signal_ts[sig] = now
        self._reason = sig.name
        self._event.set()
        _log.info(
            "shutdown_signal_received",
            signal=sig.name,
            pid=os.getpid(),
        )

    def _on_signal_sync(self, signum: int, _frame: FrameType | None) -> None:
        # Only reachable when loop.add_signal_handler is unavailable.
        try:
            sig = signal.Signals(signum)
        except ValueError:  # pragma: no cover
            return
        self._on_signal(sig)


def install_sync_shutdown_logger() -> None:
    """Fallback handler for code paths that never enter an event loop.

    Logs the signal and exits with the canonical ``128 + signum`` code so
    orchestrators (systemd, k8s) see a well-formed termination.
    """

    def _handler(signum: int, _frame: FrameType | None) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:  # pragma: no cover
            name = str(signum)
        _log.info("shutdown_signal_received_sync", signal=name, pid=os.getpid())
        sys.exit(128 + signum)

    for sig in _HANDLED_SIGNALS:
        with contextlib.suppress(OSError, ValueError):  # pragma: no cover
            signal.signal(sig, _handler)


__all__ = ["ShutdownController", "install_sync_shutdown_logger"]
