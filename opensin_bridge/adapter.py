"""Bridge adapter -- contract-aware, idempotency-aware RPC client.

Sits between the worker and the raw JSON-RPC transport (whatever
``bridge_retry.call_bridge`` is). Adds:

* Method validation against the v1 contract.
* Error normalisation to :class:`BridgeError`.
* Retry policy derived from the method's ``retry_hint``.
* Trace emission through ``observability``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from opensin_bridge.contract import (
    BRIDGE_CONTRACT_VERSION,
    BridgeError,
    ContractMismatch,
    classify_error,
    get_method,
    retry_hint_for,
)

RpcCall = Callable[[str, dict[str, Any]], Awaitable[Any]]
TraceSink = Callable[[dict[str, Any]], None]


@dataclass
class BridgeCallResult:
    method: str
    ok: bool
    value: Any = None
    error: BridgeError | None = None
    attempts: int = 1
    duration_ms: float = 0.0
    trace_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class BridgeAdapter:
    """Stateful bridge client. Instantiate one per worker session."""

    _EXPECTED_MAJOR = "1"

    def __init__(
        self,
        rpc: RpcCall,
        *,
        trace_sink: TraceSink | None = None,
        max_retries: int = 2,
        retry_backoff: float = 0.35,
    ) -> None:
        self._rpc = rpc
        self._trace_sink = trace_sink or (lambda _evt: None)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._contract_checked = False

    async def ensure_contract(self) -> None:
        if self._contract_checked:
            return
        try:
            info = await self._rpc("bridge.contract.version", {})
        except Exception as exc:
            raise classify_error(exc) from exc
        got = (info or {}).get("version", "0.0.0")
        got_major = got.split(".", 1)[0]
        if got_major != self._EXPECTED_MAJOR:
            raise ContractMismatch(BRIDGE_CONTRACT_VERSION, got)
        self._contract_checked = True

    async def call(self, method: str, params: dict[str, Any] | None = None) -> BridgeCallResult:
        spec = get_method(method)  # raises METHOD_NOT_FOUND if unknown
        params = params or {}
        trace_id = f"wrk-{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        hint = retry_hint_for(method)
        max_attempts = 1 if hint == "abort" or not spec.idempotent else self._max_retries + 1

        last_error: BridgeError | None = None
        attempt = 0
        for attempt in range(1, max_attempts + 1):
            self._trace_sink(
                {
                    "evt": "bridge.call.start",
                    "trace_id": trace_id,
                    "method": method,
                    "attempt": attempt,
                    "idempotent": spec.idempotent,
                }
            )
            try:
                value = await self._rpc(method, params)
                duration = (time.perf_counter() - started) * 1000.0
                self._trace_sink(
                    {
                        "evt": "bridge.call.ok",
                        "trace_id": trace_id,
                        "method": method,
                        "attempt": attempt,
                        "duration_ms": duration,
                    }
                )
                return BridgeCallResult(
                    method=method,
                    ok=True,
                    value=value,
                    attempts=attempt,
                    duration_ms=duration,
                    trace_id=trace_id,
                )
            except Exception as raw:
                err = classify_error(raw)
                last_error = err
                self._trace_sink(
                    {
                        "evt": "bridge.call.err",
                        "trace_id": trace_id,
                        "method": method,
                        "attempt": attempt,
                        "code": err.code,
                        "retry_hint": err.retry_hint,
                    }
                )
                if err.retry_hint == "abort" or attempt >= max_attempts:
                    break
                await asyncio.sleep(self._retry_backoff * attempt)

        return BridgeCallResult(
            method=method,
            ok=False,
            error=last_error,
            attempts=attempt,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            trace_id=trace_id,
        )

    async def __call__(self, method: str, **params: Any) -> Any:
        """Convenience: call and unwrap, raising BridgeError on failure."""
        res = await self.call(method, params)
        if res.ok:
            return res.value
        assert res.error is not None
        raise res.error
