import asyncio

from opensin_stealth import HumanizedStrategy, PassiveStrategy, register, registry, select


def test_registry_defaults():
    names = set(registry().keys())
    assert {"passive", "humanized"} <= names


def test_passive_noop():
    res = asyncio.run(PassiveStrategy().pre_action({}))
    assert res == {"delay_ms": 0}


def test_humanized_uses_bridge_assessment():
    async def fake_bridge(method, **params):
        if method == "stealth.assess":
            return {"coherence": 0.3}
        raise AssertionError(method)

    strat = HumanizedStrategy(min_coherence=0.6)
    res = asyncio.run(strat.pre_action({"bridge": fake_bridge, "tab_id": 1}))
    assert res["abort"] is True
    assert res["assessment"]["coherence"] == 0.3


def test_register_new_strategy():
    class Noop:
        name = "noop-xyz"

        async def pre_action(self, ctx):
            return {"delay_ms": 0}

        async def on_challenge(self, ctx):
            return "surface"

    register(Noop())
    assert select("noop-xyz").name == "noop-xyz"
