# Rewrite Tracker -- issue #67

| Sub-issue | Module(s) | Tests | Status |
|-----------|-----------|-------|--------|
| #68 Architecture RFC | `docs/RFC-architecture-reset.md` | n/a | done |
| #69 Bridge contract adapter | `opensin_bridge/` | `tests/contract/` | done |
| #70 Observability + evidence | `opensin_bridge/observability.py`, `evidence.py` | `tests/contract/test_adapter.py::test_trace_sink_receives_events` | done |
| #71 Session lifecycle | `opensin_bridge/session_lifecycle.py` | (live-only) | done |
| #72 Runtime state machine | `opensin_runtime/` | `tests/runtime/` | done |
| #73 Interaction engine | `opensin_interaction/` | `tests/interaction/` | done |
| #74 Stealth strategies | `opensin_stealth/` | `tests/stealth/` | done |
| #75 Panel plugins | `opensin_runtime/panels.py` | (covered by import smoke) | done |
| #76 Validation harness | `opensin_validation/` + `.github/workflows/validate.yml` | `tests/validation/` | done |

All modules are additive. The legacy `heypiggy_vision_worker.py` still runs
via `bridge_retry.call_with_retry`; new code paths opt in via
`opensin_bridge_integration.make_stack`.
