"""Unit tests for :mod:`worker.retry`."""

from __future__ import annotations

import asyncio
import random

import pytest

from worker.exceptions import BridgeTimeoutError, VisionRateLimitError
from worker.retry import RetryPolicy, retry


class TestRetryPolicy:
    def test_monotonic_backoff(self) -> None:
        policy = RetryPolicy(base_delay=1.0, backoff=2.0, jitter=0)
        delays = [policy.compute_delay(i) for i in range(1, 5)]
        assert delays == [1.0, 2.0, 4.0, 8.0]

    def test_max_delay_cap(self) -> None:
        policy = RetryPolicy(base_delay=1.0, backoff=10.0, max_delay=5.0, jitter=0)
        assert policy.compute_delay(3) == 5.0

    def test_jitter_within_bounds(self) -> None:
        policy = RetryPolicy(base_delay=1.0, backoff=1.0, jitter=0.2)
        rng = random.Random(1234)
        for _ in range(100):
            delay = policy.compute_delay(1, rng=rng)
            assert 0.8 <= delay <= 1.2

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_rejects_bad_attempts(self, attempts: int) -> None:
        with pytest.raises(ValueError, match="attempts"):
            RetryPolicy(attempts=attempts)

    def test_rejects_backoff_less_than_one(self) -> None:
        with pytest.raises(ValueError, match="backoff"):
            RetryPolicy(backoff=0.5)

    @pytest.mark.parametrize("jitter", [-0.1, 1.5])
    def test_rejects_bad_jitter(self, jitter: float) -> None:
        with pytest.raises(ValueError, match="jitter"):
            RetryPolicy(jitter=jitter)

    def test_rejects_negative_delay(self) -> None:
        with pytest.raises(ValueError, match="delays"):
            RetryPolicy(base_delay=-1)


class TestRetryDecorator:
    async def test_succeeds_first_try(self) -> None:
        calls = 0

        @retry(attempts=3, base_delay=0)
        async def f() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        assert await f() == "ok"
        assert calls == 1

    async def test_retries_on_listed_exception(self) -> None:
        calls = 0

        @retry(attempts=3, base_delay=0, retry_on=(BridgeTimeoutError,))
        async def flaky() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise BridgeTimeoutError("timeout", attempt=calls)
            return "ok"

        assert await flaky() == "ok"
        assert calls == 3

    async def test_does_not_retry_other_exceptions(self) -> None:
        calls = 0

        @retry(attempts=3, base_delay=0, retry_on=(BridgeTimeoutError,))
        async def f() -> str:
            nonlocal calls
            calls += 1
            raise VisionRateLimitError("quota")

        with pytest.raises(VisionRateLimitError):
            await f()
        assert calls == 1

    async def test_reraise_wins_over_retry(self) -> None:
        calls = 0

        @retry(
            attempts=5,
            base_delay=0,
            retry_on=(Exception,),
            reraise_on=(KeyboardInterrupt,),
        )
        async def f() -> None:
            nonlocal calls
            calls += 1
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            await f()
        assert calls == 1

    async def test_reraises_after_exhaust(self) -> None:
        calls = 0

        @retry(attempts=2, base_delay=0)
        async def always_fails() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("bad")

        with pytest.raises(RuntimeError, match="bad"):
            await always_fails()
        assert calls == 2

    async def test_passes_through_args_kwargs(self) -> None:
        @retry(attempts=1, base_delay=0)
        async def add(a: int, b: int, *, c: int) -> int:
            return a + b + c

        assert await add(1, 2, c=3) == 6

    async def test_actually_sleeps_between_attempts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        @retry(attempts=3, base_delay=0.5, backoff=2.0, jitter=0)
        async def always_fails() -> None:
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            await always_fails()

        assert sleeps == [0.5, 1.0]

    async def test_cancelled_error_is_never_retried(self) -> None:
        """CancelledError must propagate immediately, never be retried."""
        calls = 0

        @retry(attempts=5, base_delay=0, retry_on=(Exception,))
        async def cancelled() -> None:
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await cancelled()
        assert calls == 1, "CancelledError must never be retried"

    async def test_cancelled_error_passes_through_even_with_base_exception(self) -> None:
        """Even retry_on=(BaseException,) must not capture CancelledError."""
        calls = 0

        @retry(attempts=5, base_delay=0, retry_on=(BaseException,))
        async def cancelled() -> None:
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await cancelled()
        assert calls == 1

    async def test_keyboard_interrupt_is_never_retried(self) -> None:
        calls = 0

        @retry(attempts=5, base_delay=0, retry_on=(BaseException,))
        async def interrupted() -> None:
            nonlocal calls
            calls += 1
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            await interrupted()
        assert calls == 1

    async def test_system_exit_is_never_retried(self) -> None:
        calls = 0

        @retry(attempts=5, base_delay=0, retry_on=(BaseException,))
        async def exiting() -> None:
            nonlocal calls
            calls += 1
            raise SystemExit(1)

        with pytest.raises(SystemExit):
            await exiting()
        assert calls == 1

    async def test_user_reraise_list_still_wins(self) -> None:
        """User-supplied reraise_on still works on top of the built-in list."""
        calls = 0

        class MyFatal(Exception): ...

        @retry(attempts=3, base_delay=0, retry_on=(Exception,), reraise_on=(MyFatal,))
        async def f() -> None:
            nonlocal calls
            calls += 1
            raise MyFatal("don't retry me")

        with pytest.raises(MyFatal):
            await f()
        assert calls == 1
