# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Hardened `worker/` package** — consumed external feedback and tightened typing + docs:
  - `worker.cli` now has proper `BoundLogger` typing throughout (no more `log: object` +
    `# type: ignore[attr-defined]` soup) and uses `log.exception(...)` in the
    top-level except-blocks so stack traces are preserved in structured logs.
  - `worker.shutdown` replaced two runtime `assert self._loop is not None`
    invariants with explicit `RuntimeError` checks — assertions get stripped
    under `python -O`, which would silently corrupt state.
  - `worker.retry` now guarantees that `asyncio.CancelledError`,
    `KeyboardInterrupt` and `SystemExit` are **never** retried, even if the
    caller passes `retry_on=(BaseException,)`. Retrying a cancelled task
    would deadlock the event loop; retrying a SIGINT would swallow it.
  - `worker.__init__` exports the full public API (`run_worker`,
    `RetryPolicy`, `AuditLogger`, `ShutdownController`, every exception
    class, `configure_logging`, `get_logger`).
- **LICENSE** and **SECURITY.md** are no longer empty — MIT license text
  is present, and `SECURITY.md` documents a complete coordinated
  vulnerability disclosure policy (scope, channels, response targets,
  hardening baseline).
- **README.md** quick-start uses the new `heypiggy-worker` CLI, the
  Python-support badge reflects the real 3.11 / 3.12 / 3.13 support
  matrix, and a `Development` + `Exit Codes` section was added.
- **Dockerfile** — OCI image labels added, `PYTHONHASHSEED=random` set in
  runtime stage, and the brittle `pip install --no-deps || fallback`
  pattern replaced by a single dependency-resolving install that fails
  loudly on real errors.
- **GitHub Actions `ci.yml`** — `bandit` now runs in the lint job, JUnit
  artefacts are uploaded per matrix entry, and `ruff` emits GitHub
  annotations.
- **`.gitignore`** — added `!.pcpm/sessions/**` override so the session
  summaries ship with the repo (previously the blanket `sessions/` rule
  matched them).

### Added

- `tests/worker/test_public_api.py` — guards the top-level re-export
  surface (`__all__` shape, sortedness, semver-shaped version, star-import
  coverage).
- Additional `tests/worker/test_retry.py` cases for the new
  cancellation-safety guarantees (CancelledError, KeyboardInterrupt,
  SystemExit).
- Additional `tests/worker/test_shutdown.py` cases for safe restore
  before `__aenter__` and the programmer-error path when `_install_handlers`
  runs without an active context.

### Added

- New `worker/` package with typed, strict-mypy modules:
  - `worker.context` — `WorkerContext` DI container (replaces 32+ module globals).
  - `worker.logging` — `structlog`-based JSON logs with run-id correlation and secret redaction.
  - `worker.audit` — append-only JSONL audit log for compliance.
  - `worker.exceptions` — explicit error hierarchy (no more bare `except Exception`).
  - `worker.retry` — dependency-free async retry decorator with jitter.
  - `worker.shutdown` — cooperative SIGTERM/SIGINT handler with double-tap hard-exit.
  - `worker.telemetry` — optional OpenTelemetry tracing (opt-in via `OTEL_ENABLED`).
  - `worker.cli` — new `heypiggy-worker` console script (`run`, `doctor`, `version`).
  - `worker.loop` — orchestrator that wraps the legacy coroutine with preflight + audit + shutdown.
- `python -m worker` entry point.
- `.env.example` with every supported environment variable documented.
- `CHANGELOG.md` (this file).
- GitHub Actions: `ci.yml` (lint + mypy + pytest matrix + Docker build), `security.yml`
  (bandit, pip-audit, detect-secrets).
- `dependabot.yml` for pip, GitHub Actions, and Docker.
- PR template + structured issue forms (bug, feature).
- `.pre-commit-config.yaml`, `.editorconfig`.

### Changed

- **Breaking (container):** Dockerfile rewritten from a broken Node.js base to a
  hardened Python 3.13 multi-stage image with non-root user, `tini` PID 1 and
  an import-based HEALTHCHECK.
- `.gitignore` expanded to cover Python, coverage, IDE and runtime artefact noise.
- `.dockerignore` added to keep the build context small.
- `README.md` left untouched in content but now matches the new CI badge target.

### Security

- Pre-commit hooks (`.githooks/`) block secret leaks and external-source references.
- Runtime container drops to a dedicated unprivileged user (`app`, UID 10001).
- `detect-secrets` baseline scanned on every push + PR.

## [2.0.0] — initial public baseline

First public release of the HeyPiggy Vision Worker as part of the OpenSIN-AI
A2A ecosystem. Vision-gate loop, NVIDIA fail analysis, self-healing memory,
circuit breaker, typed config and Ring-Buffer recorder.

[Unreleased]: https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy/releases/tag/v2.0.0
