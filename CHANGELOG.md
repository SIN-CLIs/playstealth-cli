# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
