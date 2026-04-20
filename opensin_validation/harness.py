"""Validation harness for bridge + worker contract parity."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

from opensin_bridge.contract import METHODS, BRIDGE_CONTRACT_VERSION


@dataclass
class ValidationReport:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": self.checks}


class ValidationHarness:
    """Static and live checks for bridge parity."""

    def __init__(self, *, bridge_contract_path: pathlib.Path | None = None) -> None:
        self._bridge_contract_path = bridge_contract_path

    def static(self) -> ValidationReport:
        checks: list[dict[str, Any]] = []
        # All method names are namespaced except legacy bridge.contract.*.
        for m in METHODS:
            checks.append(
                {
                    "name": f"method.shape::{m.name}",
                    "ok": "." in m.name,
                    "detail": m.name,
                }
            )
        # Every method has a valid retry_hint literal.
        allowed_hints = {"retry", "retry-after-refresh", "retry-after-reauth", "abort"}
        for m in METHODS:
            checks.append(
                {
                    "name": f"method.retry_hint::{m.name}",
                    "ok": m.retry_hint in allowed_hints,
                    "detail": f"retry_hint={m.retry_hint}",
                }
            )
        # Read-only meta methods must not advertise mutates.
        for m in METHODS:
            if m.category == "meta":
                checks.append(
                    {
                        "name": f"method.meta_readonly::{m.name}",
                        "ok": not m.mutates,
                        "detail": f"mutates={m.mutates}",
                    }
                )
        ok = all(c["ok"] for c in checks)
        return ValidationReport(ok=ok, checks=checks)

    async def live(self, rpc) -> ValidationReport:
        checks: list[dict[str, Any]] = []
        try:
            info = await rpc("bridge.contract.version", {})
            got = (info or {}).get("version", "")
            checks.append(
                {
                    "name": "bridge.contract.version",
                    "ok": got.startswith(BRIDGE_CONTRACT_VERSION.split(".", 1)[0] + "."),
                    "detail": got,
                }
            )
        except Exception as exc:
            checks.append({"name": "bridge.contract.version", "ok": False, "detail": str(exc)})
        ok = all(c["ok"] for c in checks)
        return ValidationReport(ok=ok, checks=checks)

    @staticmethod
    def emit(report: ValidationReport, path: str = "reports/validation.json") -> pathlib.Path:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report.to_dict(), indent=2))
        return p
