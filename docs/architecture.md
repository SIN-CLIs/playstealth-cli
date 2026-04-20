# Architecture (post-reset)

See `docs/RFC-architecture-reset.md` for the rationale.

## Module map

| Module | Issue | Responsibility |
|--------|-------|----------------|
| `opensin_bridge/contract.py`       | #69 | Method registry, error codes, retry hints, version |
| `opensin_bridge/adapter.py`        | #69 | Contract-aware RPC client with retry + trace |
| `opensin_bridge/observability.py`  | #70 | TraceRecorder (JSONL per session) |
| `opensin_bridge/evidence.py`       | #70 | Pulls `bridge.evidenceBundle` + screenshot sidecar |
| `opensin_bridge/session_lifecycle.py` | #71 | Manifest, health, LKG, invalidate |
| `opensin_runtime/state_machine.py` | #72 | Deterministic IDLE->DONE/FAILED FSM |
| `opensin_runtime/panels.py`        | #75 | Loads panel plugins from `platforms/registry.json` |
| `opensin_interaction/engine.py`    | #73 | click/type/scroll with snapshot-based retry |
| `opensin_stealth/strategy.py`      | #74 | Pluggable pre-action + challenge strategies |
| `opensin_validation/harness.py`    | #76 | Static + live contract parity checks |
| `opensin_bridge_integration.py`    | -   | Shim for legacy `heypiggy_vision_worker.py` |

## Adopting the new stack from the legacy worker

```python
from bridge_retry import call_with_retry
from opensin_bridge_integration import make_stack, wrap_legacy_call_with_retry

rpc_factory = wrap_legacy_call_with_retry(call_with_retry)
rpc = rpc_factory(mcp_call_you_already_have)
stack = make_stack(rpc, session_id="survey-42")

await stack.bridge.ensure_contract()
state = stack.state
state.transition(RuntimeState.PLANNING)
result = await stack.engine.act(ActionPlan(verb="click", target_key="Submit"))
```
