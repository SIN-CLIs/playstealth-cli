import asyncio

import pytest

from opensin_bridge.adapter import BridgeAdapter
from opensin_bridge.contract import BridgeError, ContractMismatch


class FakeRpc:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def __call__(self, method, params):
        self.calls.append((method, params))
        answer = self.answers.get(method, [])
        if callable(answer):
            return answer(params)
        if not answer:
            raise BridgeError("INTERNAL", "no answer configured", retry_hint="retry")
        head = answer.pop(0)
        if isinstance(head, Exception):
            raise head
        return head


def test_ensure_contract_accepts_major_match():
    rpc = FakeRpc({"bridge.contract.version": [{"version": "1.2.3"}]})
    adapter = BridgeAdapter(rpc)
    asyncio.run(adapter.ensure_contract())
    # second call should be a no-op (cached)
    asyncio.run(adapter.ensure_contract())
    assert len(rpc.calls) == 1


def test_ensure_contract_rejects_major_mismatch():
    rpc = FakeRpc({"bridge.contract.version": [{"version": "2.0.0"}]})
    adapter = BridgeAdapter(rpc)
    with pytest.raises(ContractMismatch):
        asyncio.run(adapter.ensure_contract())


def test_call_retries_idempotent_then_succeeds():
    rpc = FakeRpc(
        {
            "dom.snapshot": [
                BridgeError("TARGET_NOT_FOUND", "a", retry_hint="retry"),
                {"nodes": [{"name": "ok"}]},
            ]
        }
    )
    adapter = BridgeAdapter(rpc, retry_backoff=0)
    res = asyncio.run(adapter.call("dom.snapshot", {}))
    assert res.ok
    assert res.attempts == 2


def test_call_does_not_retry_non_idempotent():
    rpc = FakeRpc(
        {
            "dom.click": [
                BridgeError("TARGET_NOT_FOUND", "gone", retry_hint="retry-after-refresh"),
                BridgeError("TARGET_NOT_FOUND", "still gone", retry_hint="retry-after-refresh"),
            ]
        }
    )
    adapter = BridgeAdapter(rpc, retry_backoff=0)
    res = asyncio.run(adapter.call("dom.click", {}))
    assert not res.ok
    assert res.attempts == 1  # dom.click is non-idempotent -> no retry loop
    assert res.error is not None
    assert res.error.code == "TARGET_NOT_FOUND"


def test_call_aborts_on_abort_hint():
    rpc = FakeRpc(
        {
            "dom.snapshot": [
                BridgeError("CHALLENGE_DETECTED", "cf", retry_hint="abort"),
                {"nodes": []},
            ]
        }
    )
    adapter = BridgeAdapter(rpc, retry_backoff=0)
    res = asyncio.run(adapter.call("dom.snapshot", {}))
    assert not res.ok
    assert res.attempts == 1
    assert res.error.code == "CHALLENGE_DETECTED"


def test_call_rejects_unknown_method():
    rpc = FakeRpc({})
    adapter = BridgeAdapter(rpc)
    with pytest.raises(BridgeError) as excinfo:
        asyncio.run(adapter.call("does.not.exist", {}))
    assert excinfo.value.code == "METHOD_NOT_FOUND"


def test_trace_sink_receives_events():
    events = []
    rpc = FakeRpc({"dom.snapshot": [{"nodes": []}]})
    adapter = BridgeAdapter(rpc, trace_sink=events.append)
    res = asyncio.run(adapter.call("dom.snapshot", {}))
    assert res.ok
    kinds = {e["evt"] for e in events}
    assert "bridge.call.start" in kinds
    assert "bridge.call.ok" in kinds
