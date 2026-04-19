# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.x     | :white_check_mark: |
| 1.x     | :x:                |

Only the latest minor release of the 2.x series receives security fixes.
Older releases are end-of-life and will not be patched.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security problems.**

To report a vulnerability please use one of the following channels:

- GitHub Private Vulnerability Reporting:
  <https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy/security/advisories/new>
- Email: `security@opensin.ai` (PGP on request)

Please include as much of the following as you can:

- A clear description of the issue and its impact.
- Steps to reproduce (minimal proof-of-concept welcome).
- The affected commit hash, release tag, or Docker image digest.
- Your preferred name / handle for the acknowledgement (or ask to stay anonymous).

## Response Targets

We aim for the following turnaround on valid reports:

| Phase               | Target                 |
| ------------------- | ---------------------- |
| Initial reply       | within 3 business days |
| Triage + severity   | within 7 business days |
| Fix / mitigation    | within 30 days         |
| Coordinated release | mutually agreed        |

Critical issues (remote code execution, credential exfiltration, privilege
escalation) are triaged out-of-band and get emergency releases.

## Scope

In scope:

- The `worker/` package (new modular code).
- The legacy `heypiggy_vision_worker.py` monolith and its helper modules
  (`circuit_breaker.py`, `config.py`, `fail_*`, `nvidia_video_analyzer.py`,
  `observability.py`, `state_machine.py`).
- The published Docker image (`heypiggy-worker`).
- The CI/CD configuration in `.github/workflows/`.

Out of scope:

- Findings that require physical access to the host running the worker.
- Social-engineering or phishing scenarios against maintainers.
- Denial of service achieved only by exhausting the caller's own NVIDIA
  NIM quota.
- Issues in third-party dependencies without a working exploit against
  this project. Please report those to the upstream project first and
  then file an issue here linking to the advisory.

## Hardening Baseline

Each release is gated by the following automated checks (see
`.github/workflows/`):

- `ruff` lint + format (strict rules on `worker/`).
- `mypy --strict` on `worker/`.
- `pytest` on Python 3.11, 3.12, 3.13 with coverage >= 80 %.
- `bandit` static analysis.
- `pip-audit` against the pinned runtime dependencies.
- `detect-secrets` scan against `.secrets.baseline`.

The runtime Docker image ships as a non-root user (`app`, UID 10001) on
`python:*-slim` with `tini` as PID 1 for clean signal forwarding.

## Credit

We publish a `SECURITY_ADVISORIES.md` changelog entry for every
coordinated disclosure and credit reporters who opt in.
