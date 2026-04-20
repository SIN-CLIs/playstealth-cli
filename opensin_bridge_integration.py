# ================================================================================
# DATEI: opensin_bridge_integration.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Legacy <-> new-stack bridge.

``heypiggy_vision_worker.py`` keeps working unchanged. New code paths can
import :func:`make_stack` to get a fully wired
``BridgeAdapter + InteractionEngine + StateMachine + TraceRecorder`` tuple
that delegates raw RPC to the existing ``bridge_retry.call_with_retry``.

This module is the single integration point between the architecture
reset (issues #68-#76) and the in-place worker. Delete once the worker
no longer needs the legacy ``call_with_retry`` signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from opensin_bridge.adapter import BridgeAdapter
from opensin_bridge.observability import TraceRecorder
from opensin_interaction import InteractionEngine
from opensin_runtime import StateMachine


RpcFn = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class OpenSinStack:
    # ========================================================================
    # KLASSE: OpenSinStack
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    bridge: BridgeAdapter
    engine: InteractionEngine
    state: StateMachine
    recorder: TraceRecorder


def wrap_legacy_call_with_retry(call_with_retry) -> RpcFn:
    """Adapt ``bridge_retry.call_with_retry`` to the new ``RpcFn`` shape.

    The legacy signature is roughly::

        async def call_with_retry(mcp_call, tool_name, params, ...) -> dict

    We expose the new ``(method, params) -> value`` contract by binding
    the caller-provided ``mcp_call`` once and translating the legacy
    result envelope to either a value or a raised ``BridgeError``.
    """
    from opensin_bridge.contract import classify_error

    def factory(mcp_call) -> RpcFn:
        async def _call(method: str, params: dict[str, Any]) -> Any:
            result = await call_with_retry(mcp_call, method, params or {})
            if isinstance(result, dict) and result.get("error"):
                raise classify_error(result["error"] if isinstance(result["error"], dict) else {"code": "INTERNAL", "message": str(result["error"])})
            if isinstance(result, dict) and result.get("ok") is False:
                raise classify_error({"code": "INTERNAL", "message": str(result.get("reason") or result)})
            if isinstance(result, dict) and "value" in result and set(result.keys()) <= {"ok", "value"}:
                return result["value"]
            return result

        return _call

    return factory


def make_stack(rpc: RpcFn, *, session_id: str | None = None) -> OpenSinStack:
    recorder = TraceRecorder(session_id=session_id)
    bridge = BridgeAdapter(rpc, trace_sink=recorder.emit)
    engine = InteractionEngine(bridge)
    state = StateMachine(on_transition=lambda t: recorder.emit({"evt": "state", "src": t.src.value, "dst": t.dst.value, "reason": t.reason}))
    return OpenSinStack(bridge=bridge, engine=engine, state=state, recorder=recorder)
