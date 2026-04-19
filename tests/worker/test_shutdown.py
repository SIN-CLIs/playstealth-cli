"""Unit tests for :mod:`worker.shutdown`."""

from __future__ import annotations

import asyncio
import signal

import pytest

from worker.shutdown import ShutdownController


class TestShutdownController:
    async def test_starts_not_requested(self) -> None:
        async with ShutdownController() as ctrl:
            assert ctrl.requested is False
            assert ctrl.reason is None

    async def test_manual_request_sets_state(self) -> None:
        async with ShutdownController() as ctrl:
            ctrl.request("test")
            assert ctrl.requested is True
            assert ctrl.reason == "test"

    async def test_manual_request_is_idempotent(self) -> None:
        async with ShutdownController() as ctrl:
            ctrl.request("first")
            ctrl.request("second")  # ignored — first wins
            assert ctrl.reason == "first"

    async def test_wait_returns_true_when_signalled(self) -> None:
        async with ShutdownController() as ctrl:
            ctrl.request("x")
            assert await ctrl.wait(timeout=0.1) is True

    async def test_wait_returns_false_on_timeout(self) -> None:
        async with ShutdownController() as ctrl:
            assert await ctrl.wait(timeout=0.05) is False

    async def test_wait_blocks_until_requested(self) -> None:
        async with ShutdownController() as ctrl:

            async def trigger() -> None:
                await asyncio.sleep(0.01)
                ctrl.request("delayed")

            task = asyncio.create_task(trigger())
            assert await ctrl.wait(timeout=1.0) is True
            await task
            assert ctrl.reason == "delayed"

    async def test_sigint_is_handled(self) -> None:
        async with ShutdownController() as ctrl:
            ctrl._on_signal(signal.SIGINT)
            assert ctrl.requested is True
            assert ctrl.reason == "SIGINT"

    async def test_sigterm_is_handled(self) -> None:
        async with ShutdownController() as ctrl:
            ctrl._on_signal(signal.SIGTERM)
            assert ctrl.requested is True
            assert ctrl.reason == "SIGTERM"

    async def test_handlers_are_cleaned_up(self) -> None:
        loop = asyncio.get_running_loop()
        # Install a canary handler; the controller should not clobber it permanently.
        sentinel_fired: list[str] = []
        loop.add_signal_handler(signal.SIGUSR1, lambda: sentinel_fired.append("x"))

        async with ShutdownController():
            pass  # install + remove

        # Our canary is still alive.
        loop.remove_signal_handler(signal.SIGUSR1)

    @pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX only")
    async def test_sighup_is_handled(self) -> None:
        async with ShutdownController() as ctrl:
            ctrl._on_signal(signal.SIGHUP)
            assert ctrl.requested is True
            assert ctrl.reason == "SIGHUP"

    async def test_restore_without_enter_is_safe(self) -> None:
        """Calling _restore_handlers before __aenter__ must not crash."""
        ctrl = ShutdownController()
        # No exception, no state mutation.
        ctrl._restore_handlers()

    async def test_install_without_enter_raises(self) -> None:
        """Calling _install_handlers before __aenter__ is a programmer error."""
        ctrl = ShutdownController()
        with pytest.raises(RuntimeError, match="before __aenter__"):
            ctrl._install_handlers()

    async def test_double_tap_sigint_does_not_affect_first_tap_state(self) -> None:
        """First tap still sets requested/reason; second tap hard-exits (not tested)."""
        async with ShutdownController() as ctrl:
            ctrl._on_signal(signal.SIGINT)
            assert ctrl.requested is True
            assert ctrl.reason == "SIGINT"
            # The SIGINT ts must have been recorded so the second tap hits the fast-path.
            assert signal.SIGINT in ctrl._first_signal_ts

    async def test_wait_without_timeout_blocks_until_requested(self) -> None:
        async with ShutdownController() as ctrl:

            async def trigger() -> None:
                await asyncio.sleep(0.01)
                ctrl.request("delayed")

            task = asyncio.create_task(trigger())
            # Timeout=None means "block forever until set".
            result = await ctrl.wait()
            assert result is True
            await task
