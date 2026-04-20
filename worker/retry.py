# ================================================================================
# DATEI: retry.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Generic async retry decorator with exponential backoff + jitter.

Used by the bridge and vision clients. Intentionally dependency-free —
does not pull in :mod:`tenacity` to keep the runtime footprint small.

Example::

    from worker.retry import retry
    from worker.exceptions import BridgeTimeoutError

    @retry(attempts=3, retry_on=(BridgeTimeoutError,))
    async def call_bridge(...) -> str: ...

Cancellation is handled correctly: :class:`asyncio.CancelledError` and
:class:`SystemExit` / :class:`KeyboardInterrupt` are **never** retried —
even if the caller adds :class:`BaseException` to ``retry_on``. Retrying a
cancelled task would silently deadlock the event loop, so the decorator
always re-raises these to protect the shutdown path.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Final, TypeVar, cast

from worker.logging import get_logger

_log = get_logger(__name__)

T = TypeVar("T")
AsyncFn = Callable[..., Awaitable[T]]

#: Exceptions that are always re-raised, regardless of ``retry_on``. Retrying
#: these would break cooperative cancellation and signal handling.
_ALWAYS_RERAISE: Final[tuple[type[BaseException], ...]] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    # ========================================================================
    # KLASSE: RetryPolicy
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Static description of a retry policy."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 10.0
    backoff: float = 2.0
    jitter: float = 0.2  # ± fraction of current delay

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be non-negative")
        if self.backoff < 1:
            raise ValueError("backoff must be >= 1")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be in [0, 1]")

    def compute_delay(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Delay (seconds) before attempt number ``attempt`` (1-indexed)."""
        raw = self.base_delay * (self.backoff ** (attempt - 1))
        raw = min(raw, self.max_delay)
        if self.jitter:
            uniform = rng.uniform if rng is not None else random.uniform
            raw *= 1 + uniform(-self.jitter, self.jitter)
        return max(0.0, raw)


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    backoff: float = 2.0,
    jitter: float = 0.2,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    reraise_on: tuple[type[BaseException], ...] = (),
) -> Callable[[AsyncFn[T]], AsyncFn[T]]:
    """Decorate an async callable with exponential-backoff retries.

    Args:
        attempts: Total attempts including the first. ``attempts=1`` disables
            retry entirely.
        base_delay: Initial delay before the second attempt.
        max_delay: Hard cap for any single backoff sleep.
        backoff: Multiplicative factor between attempts.
        jitter: Fractional jitter (``0.2`` = ±20 %).
        retry_on: Exceptions that trigger a retry.
        reraise_on: Exceptions that are *never* retried, even if they also
            match ``retry_on``. Takes precedence.

    Returns:
        The decorated callable.
    """
    policy = RetryPolicy(
        attempts=attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        backoff=backoff,
        jitter=jitter,
    )
    # Cancellation + top-level signals are never retryable, no matter what
    # the caller passed in. Prepend them so they always win the race.
    effective_reraise: tuple[type[BaseException], ...] = _ALWAYS_RERAISE + tuple(reraise_on)

    def decorator(fn: AsyncFn[T]) -> AsyncFn[T]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, policy.attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except effective_reraise:
                    raise
                except retry_on as exc:
                    last_exc = exc
                    if attempt >= policy.attempts:
                        _log.warning(
                            "retry_exhausted",
                            function=fn.__qualname__,
                            attempts=attempt,
                            error=type(exc).__name__,
                            error_message=str(exc),
                        )
                        raise
                    delay = policy.compute_delay(attempt)
                    _log.info(
                        "retry_scheduled",
                        function=fn.__qualname__,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        delay_seconds=round(delay, 3),
                        error=type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
            # Unreachable — the loop either returns or raises.
            raise cast(BaseException, last_exc)  # pragma: no cover

        return wrapper

    return decorator


__all__ = ["RetryPolicy", "retry"]
