import asyncio

from opensin_validation import ValidationHarness


def test_static_report_is_green():
    report = ValidationHarness().static()
    failures = [c for c in report.checks if not c["ok"]]
    assert report.ok, failures
    assert len(report.checks) > 50


def test_live_probe_accepts_match():
    async def rpc(method, params):
        return {"version": "1.5.0"}

    r = asyncio.run(ValidationHarness().live(rpc))
    assert r.ok


def test_live_probe_rejects_mismatch():
    async def rpc(method, params):
        return {"version": "2.0.0"}

    r = asyncio.run(ValidationHarness().live(rpc))
    assert not r.ok
