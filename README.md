# A2A-SIN-Worker-heypiggy

Autonomous Python worker that logs into **heypiggy.com**, opens surveys
one after another, and fills them out correctly — earning EUR per
completion. Vision-guided by NVIDIA NIM's Llama-3.2-Vision with a full
fallback stack and a learn-from-failure Global Brain integration.

> **AI coding agents (opencode / Claude Code / Cursor / Aider / v0):**
> start with [AGENTS.md](AGENTS.md). It is the authoritative runbook.

---

## Quick start

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Required env
export NVIDIA_API_KEY="nvapi-..."
export HEYPIGGY_EMAIL="you@example.com"
export HEYPIGGY_PASSWORD="yourpassword"
export BRIDGE_MCP_URL="http://127.0.0.1:7777"

# Sanity check
heypiggy-worker doctor

# Run
heypiggy-worker run
```

After a run, check `/tmp/heypiggy_<runid>/run_summary.json` for
`earnings_eur`, `surveys_completed`, and `surveys_disqualified`.

---

## Features

- **Vision-guided autonomy.** Every click is gated by Llama-3.2-Vision
  reading a DOM-prescan + screenshot — 16 scanner blocks detect
  questions, matrices, sliders, spinners, required fields, DQ signals,
  EUR rewards, and more.
- **Multi-modal.** Audio questions are transcribed via NVIDIA Parakeet;
  video questions summarized via Cosmos-Reason1; images go straight
  into the vision prompt.
- **Multi-survey queue.** Auto-detects "Next Survey" buttons, scores
  dashboard cards by reward, skips known-DQ URLs via the Global Brain.
- **Self-healing.** Same-question loop detection, infinite-spinner
  recovery, click-escalation ladder, human-typing cadence.
- **Session persistence.** Cookies + localStorage cached across runs
  (72h TTL, mode 0600) — no login dance every start.
- **Panel-aware.** Built-in overrides for PureSpectrum, Dynata, Sapio,
  Cint, Lucid, and HeyPiggy itself.
- **Observable.** Structured JSON logs via structlog, OpenTelemetry
  export, per-run `run_summary.json`.

---

## Documentation

| File | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | **Start here** — runbook for AI agents and humans |
| [A2A-CARD.md](A2A-CARD.md) | Agent capabilities + deployment metadata |
| [CONTRIBUTING.md](CONTRIBUTING.md) | PR rules |
| [SECURITY.md](SECURITY.md) | Secret handling + disclosure |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

---

## Architecture at a glance

```
heypiggy_vision_worker.py   Main async loop + 16-block dom_prescan pipeline
worker/cli.py               heypiggy-worker CLI (run | doctor | version)
survey_orchestrator.py      Queue, auto-detect, brain-backed skip filter
panel_overrides.py          PureSpectrum / Dynata / Sapio / Cint / Lucid rules
session_store.py            Cross-run cookie + localStorage cache
persona.py                  Profile-aware answer bank for the vision prompt
audio_handler.py            NVIDIA Parakeet ASR for audio questions
video_handler.py            NVIDIA Cosmos-Reason for video questions
global_brain_client.py      PCPM fact store — learn once, skip next time
config.py                   Single source of all environment configuration
```

See [AGENTS.md §5](AGENTS.md) for the full map and [AGENTS.md §7](AGENTS.md)
for the troubleshooting decision tree.

---

## Tests

```bash
python -m pytest tests/ -q --ignore=tests/worker
```

210+ tests, typically <5s. Never commit with failures.

---

## License

MIT — see [LICENSE](LICENSE).

Part of the OpenSIN-AI ecosystem. Managed by `A2A-SIN-Team-Worker`.
