# RFC: Architecture Reset (Issue #68)

**Status:** Draft -> Accepted
**Author:** v0 on behalf of OpenSIN-AI
**Scope:** `A2A-SIN-Worker-heypiggy` + `OpenSIN-Bridge`
**Parent:** #67

## 1. Problem

The worker (`heypiggy_vision_worker.py`, ~290 kLOC in a single file) has grown into a
monolith that owns transport, state, interaction, stealth, and persona
concerns simultaneously. The bridge contract is implicit: callers pass
free-form tool names and the worker reaches directly into Chrome through a
loosely typed JSON-RPC channel.

Consequences:

1. No clear seam between orchestration (what to do) and execution
   (how to click, wait, retry). This makes the worker impossible to fuzz.
2. Failure modes leak into the A2A surface -- a stale cookie raises the same
   `RuntimeError` as a Cloudflare challenge or a closed tab.
3. Observability is after-the-fact. Replays rely on reading logs.
4. Stealth changes require editing the worker and the bridge in lockstep
   with no version negotiation, which blocks incremental rollout.

## 2. Goals

| # | Goal | Verifiable by |
|---|------|---------------|
| G1 | Explicit, versioned Bridge contract | `bridge.contract.version == "1.0.0"` on both sides |
| G2 | Deterministic runtime state machine | `tests/runtime/test_state_machine.py` |
| G3 | Evidence for every dispatch | `bridge.evidenceBundle` returns snapshot + screenshot + network + behavior |
| G4 | Session lifecycle with TTL + LKG | `session.manifest`, `session.lastKnownGood` |
| G5 | Stealth as a pluggable strategy | `opensin_stealth/strategy.py` with register/select |
| G6 | Panel plugins load from manifest | `platforms/registry.json` + `panel_overrides.py` |
| G7 | Validation harness runs in CI | `make validate` green on main |

## 3. Non-goals

- No rewrite of the vision pipeline in this RFC. The interaction engine
  consumes the same `BBox` + `target_key` payload that the LLM already
  produces.
- No migration off Playwright.
- No change to the A2A surface contract. The A2A card stays as-is; this
  RFC only reshapes what lives behind it.

## 4. Module Boundaries

```
opensin_bridge/         # issue #69 - contract adapter, idempotency
opensin_runtime/        # issue #72 - state machine (IDLE -> PLANNING -> ACTING -> ...)
opensin_interaction/    # issue #73 - click/type/scroll primitives
opensin_stealth/        # issue #74 - strategy registry
opensin_validation/     # issue #76 - contract + flake detection harness
observability.py        # issue #70 - extended with evidence bundles
session_store.py        # issue #71 - extended with manifest + TTL + LKG
panel_overrides.py      # issue #75 - plugin loader
heypiggy_vision_worker  # shrinks to orchestrator + A2A entry point
```

## 5. Dispatch Path

```
A2A request
  -> worker.handle_task                       (orchestration)
     -> opensin_runtime.StateMachine.step     (one transition per call)
        -> opensin_bridge.BridgeAdapter.call  (contract-validated)
           -> JSON-RPC to OpenSIN-Bridge Chrome extension
        -> opensin_interaction.Engine.act     (click/type/scroll with retries)
        -> opensin_stealth.Strategy.assess    (pre-action guard)
        -> observability.Recorder.emit        (trace + evidence)
     -> session_store.Manifest.refresh        (TTL, LKG, invalidate)
```

Each arrow is a seam. Tests can replace any component with a fake.

## 6. Contract negotiation

The worker calls `bridge.contract.version` once per session. If the major
version does not match the compiled-in expectation (`_EXPECTED_CONTRACT = "1"`),
the worker refuses to proceed and raises `ContractMismatch` to the A2A
caller with `retry_hint=abort`. Minor-version mismatches are logged but
accepted.

## 7. Backwards compatibility

- The flat legacy tool names (`click`, `type`, `nav_to`, ...) keep working
  via `extension/src/tools/aliases.js` in the bridge. The worker adopts
  the namespaced names (`dom.click`, `dom.type`, `navigation.to`, ...)
  module by module.
- `session_store.save_cookies` / `save_session` keep their current shape.
  The new `SessionManifest` wraps them, it does not replace them.
- `observability.emit` keeps its current signature. Evidence bundles are
  an additive API.

## 8. Rollout

Each sub-issue (#69..#76) ships on its own feature branch and is mergeable
independently. Merge order is not enforced; every branch has a green CI run
on `make validate` before it goes in.

## 9. Open questions

- Should the interaction engine own scroll-into-view retries or should the
  bridge? -> Decision: bridge owns low-level DOM, engine owns policy.
- Should stealth strategies ship in the worker or the bridge? -> Decision:
  heuristics live in the bridge (`stealth.assess`), policy lives in the
  worker (`opensin_stealth/strategy.py`).
- Where do panel plugins run? -> Decision: worker-side, loaded from
  `platforms/registry.json`.
