import asyncio

from opensin_bridge.adapter import BridgeAdapter
from opensin_bridge.contract import BridgeError
from opensin_interaction import ActionPlan, InteractionEngine


class FakeRpc:
    def __init__(self, script):
        self.script = script  # dict[method] -> list
        self.calls = []

    async def __call__(self, method, params):
        self.calls.append((method, params))
        queue = self.script.get(method, [])
        if not queue:
            raise BridgeError("INTERNAL", f"no answer for {method}", retry_hint="retry")
        head = queue.pop(0)
        if isinstance(head, Exception):
            raise head
        return head


def _ok_snapshot(name="Login"):
    return {"nodes": [{"name": name, "selector": f"[data-name={name}]"}]}


def test_engine_click_happy_path():
    rpc = FakeRpc(
        {
            "dom.snapshot": [_ok_snapshot(), _ok_snapshot()],
            "dom.click": [{"ok": True}],
        }
    )
    eng = InteractionEngine(BridgeAdapter(rpc, retry_backoff=0))
    res = asyncio.run(eng.act(ActionPlan(verb="click", target_key="Login")))
    assert res.ok
    assert res.attempts == 1


def test_engine_retries_when_target_missing():
    rpc = FakeRpc(
        {
            "dom.snapshot": [
                {"nodes": []},  # first try: not there
                _ok_snapshot(),  # second try: found
                _ok_snapshot(),  # verification
            ],
            "dom.click": [{"ok": True}],
        }
    )
    eng = InteractionEngine(BridgeAdapter(rpc, retry_backoff=0))
    res = asyncio.run(eng.act(ActionPlan(verb="click", target_key="Login", backoff=0)))
    assert res.ok
    assert res.attempts == 2


def test_engine_gives_up_after_attempts():
    rpc = FakeRpc({"dom.snapshot": [{"nodes": []}, {"nodes": []}, {"nodes": []}]})
    eng = InteractionEngine(BridgeAdapter(rpc, retry_backoff=0))
    res = asyncio.run(
        eng.act(ActionPlan(verb="click", target_key="Missing", attempts=3, backoff=0))
    )
    assert not res.ok
    assert res.error is not None
    assert res.error.code == "TARGET_NOT_FOUND"
