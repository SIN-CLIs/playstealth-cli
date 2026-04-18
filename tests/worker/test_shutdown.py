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
