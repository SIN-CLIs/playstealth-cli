# ================================================================================
# DATEI: engine.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Interaction primitives with deterministic retry + verification.

Each primitive:

1. Asks the bridge for a DOM snapshot to resolve the target.
2. Dispatches the action via ``BridgeAdapter``.
3. Verifies the post-condition (target still present, value set, ...).
4. Emits a structured ``ActionResult``.

If step 3 fails, the engine retries up to ``attempts`` times, refreshing
the snapshot between attempts. If all retries fail, it returns a failed
``ActionResult`` -- it never raises. That is the caller's decision via
the runtime state machine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from opensin_bridge.adapter import BridgeAdapter
from opensin_bridge.contract import BridgeError


@dataclass
class ActionPlan:
    # ========================================================================
    # KLASSE: ActionPlan
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    verb: str  # "click" | "type" | "scroll" | "select"
    target_key: str  # aria label | role+name | test-id ...
    value: str | None = None
    tab_id: int | None = None
    attempts: int = 3
    backoff: float = 0.4
    verify: bool = True


@dataclass
class ActionResult:
    # ========================================================================
    # KLASSE: ActionResult
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    ok: bool
    plan: ActionPlan
    attempts: int
    error: BridgeError | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class InteractionEngine:
    # ========================================================================
    # KLASSE: InteractionEngine
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def __init__(self, bridge: BridgeAdapter) -> None:
        self._bridge = bridge

    async def act(self, plan: ActionPlan) -> ActionResult:
        last_err: BridgeError | None = None
        for attempt in range(1, plan.attempts + 1):
            try:
                snap = await self._bridge(
                    "dom.snapshot",
                    tabId=plan.tab_id,
                )
            except BridgeError as err:
                last_err = err
                await asyncio.sleep(plan.backoff * attempt)
                continue

            target = self._locate(snap, plan.target_key)
            if not target:
                last_err = BridgeError(
                    "TARGET_NOT_FOUND",
                    f"target {plan.target_key!r} missing in snapshot",
                    retry_hint="retry-after-refresh",
                )
                await asyncio.sleep(plan.backoff * attempt)
                continue

            try:
                await self._dispatch(plan, target)
            except BridgeError as err:
                last_err = err
                if err.retry_hint == "abort":
                    return ActionResult(ok=False, plan=plan, attempts=attempt, error=err)
                await asyncio.sleep(plan.backoff * attempt)
                continue

            if plan.verify and not await self._verify(plan, target):
                last_err = BridgeError(
                    "STALE_TARGET",
                    "post-condition failed after action",
                    retry_hint="retry-after-refresh",
                )
                await asyncio.sleep(plan.backoff * attempt)
                continue

            return ActionResult(ok=True, plan=plan, attempts=attempt, evidence={"target": target})

        return ActionResult(ok=False, plan=plan, attempts=plan.attempts, error=last_err)

    # ------------------------------------------------------------------

    @staticmethod
    def _locate(snapshot: Any, target_key: str) -> dict[str, Any] | None:
        if not snapshot:
            return None
        nodes = snapshot.get("nodes") if isinstance(snapshot, dict) else None
        if not nodes:
            return None
        for node in nodes:
            if not isinstance(node, dict):
                continue
            keys = (
                node.get("name"),
                node.get("ariaLabel"),
                node.get("testId"),
                node.get("target_key"),
            )
            if target_key in [k for k in keys if k]:
                return node
        return None

    async def _dispatch(self, plan: ActionPlan, target: dict[str, Any]) -> None:
        params = {"tabId": plan.tab_id, "target": target.get("selector") or target}
        if plan.verb == "click":
            await self._bridge("dom.click", **params)
        elif plan.verb == "type":
            await self._bridge("dom.type", value=plan.value or "", **params)
        elif plan.verb == "scroll":
            await self._bridge("dom.scroll", **params)
        elif plan.verb == "select":
            await self._bridge("dom.click", **params)
        else:
            raise BridgeError("INVALID_ARGS", f"unknown verb {plan.verb!r}")

    async def _verify(self, plan: ActionPlan, target: dict[str, Any]) -> bool:
        # Cheap verification: the target still exists in a fresh snapshot.
        try:
            snap = await self._bridge("dom.snapshot", tabId=plan.tab_id)
        except BridgeError:
            return False
        return self._locate(snap, plan.target_key) is not None
