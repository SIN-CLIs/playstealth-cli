# AGENTS.md — A2A-SIN-Worker-heypiggy

> **This file is the single source of truth for any AI coding agent
> (opencode, Claude Code, Cursor, Aider, v0, Codex) operating on this
> repository. Read this FIRST. If instructions elsewhere contradict it,
> this file wins.**

---

## 1. 10-SECOND ORIENTATION

- **What this repo is:** an autonomous Python worker that logs into
  `heypiggy.com`, opens surveys one after another, and fills them out
  correctly until it gets paid in EUR.
- **Language:** Python 3.11+ (NOT Node/Next.js — ignore any `npm`/`pnpm`
  references you see in stale docs).
- **Entry point:** `heypiggy-worker run` (CLI) or
  `python heypiggy_vision_worker.py` (legacy direct-run).
- **Primary LLM:** NVIDIA NIM `meta/llama-3.2-11b-vision-instruct`
  (vision). Fallbacks: `microsoft/phi-3.5-vision-instruct`,
  `microsoft/phi-3-vision-128k-instruct`.
- **Browser driver:** connects to a running **Bridge MCP** (Chromium
  controller) — the worker never launches its own browser.

If the user says *"run the heypiggy worker"* / *"start surveys"* /
*"A2A-SIN-Worker-heypiggy"*, follow section 3 verbatim.

---

## 2. WHEN-THE-USER-SAYS → DO-THIS

| User says… | Do exactly this |
|---|---|
| "start the worker", "run surveys", "earn money", "A2A-SIN-Worker-heypiggy go" | Run section 3 (**Happy Path**). |
| "it failed / stuck / no surveys" | Run section 7 (**Troubleshooting**) top-down. |
| "add a new screener rule / panel", "fix Dynata" | Edit `panel_overrides.py`. Don't touch `heypiggy_vision_worker.py` for panel-specific logic. |
| "persona / profile / my data is wrong" | Edit `~/.config/heypiggy/profile.json` OR set `HEYPIGGY_PROFILE_PATH`. DO NOT hardcode persona in `.py` files. |
| "session/login broken", "logs me out every run" | Delete `~/.heypiggy/session_cache.json` and re-run — see section 6. |
| "skip surveys we already failed" | Already built — `should_skip` callback in `survey_orchestrator.py` queries the Global Brain. |
| "run tests" | `pnpm exec pytest` is wrong. Use: `python -m pytest tests/ -q --ignore=tests/worker`. |
| "add / fix a feature" | Always: (a) search the code with Grep, (b) prefer extending the relevant scanner block in `dom_prescan`, (c) run the full test suite before closing. |

---

## 3. HAPPY PATH (copy-paste this)

```bash
# One-time setup (in repo root) — uses a venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Required env vars (the worker refuses to start without these)
export NVIDIA_API_KEY="nvapi-..."
export HEYPIGGY_EMAIL="you@example.com"
export HEYPIGGY_PASSWORD="yourpassword"

# Optional but highly recommended
export BRIDGE_MCP_URL="http://127.0.0.1:7777"         # where Bridge listens
export HEYPIGGY_PERSONA="default"                     # which profile to use
export HEYPIGGY_MAX_SURVEYS="25"                      # per run
export BRAIN_URL="http://127.0.0.1:7070"              # Global Brain (optional)
export AI_GATEWAY_API_KEY="..."                       # only if not using zero-config NVIDIA

# Health check BEFORE running (exits non-zero if anything is missing)
heypiggy-worker doctor

# Actually run
heypiggy-worker run
```

**Exit codes:** `0` clean shutdown · `2` config error · `3` preflight
failed (bridge down, login broken) · `4` worker error · `130` Ctrl-C.

**Outputs after a run:**
- `/tmp/heypiggy_<runid>/run_summary.json` — totals: `earnings_eur`,
  `surveys_completed`, `surveys_disqualified`, `step_metrics`.
- `/tmp/heypiggy_<runid>/session_*.json` — per-run cookie snapshots.
- `~/.heypiggy/session_cache.json` — cross-run cookie + localStorage
  cache (auto-created, chmod 600).
- `~/.pcpm/...` — Global Brain local fallback (facts learned this run).

---

## 4. ENV VARS — COMPLETE MAP

### Required (hard-fail on startup)

| Var | Purpose |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA NIM auth. Primary vision model. |
| `HEYPIGGY_EMAIL` | Login to heypiggy.com. |
| `HEYPIGGY_PASSWORD` | Login to heypiggy.com. |

### Bridge / MCP (must point at a running Bridge)

| Var | Default |
|---|---|
| `BRIDGE_MCP_URL` | `http://127.0.0.1:7777` |
| `BRIDGE_HEALTH_URL` | `http://127.0.0.1:7777/health` |
| `BRIDGE_CONNECT_TIMEOUT` | `30` |

### Vision / Models

| Var | Default |
|---|---|
| `VISION_BACKEND` | `auto` (→ NVIDIA if `NVIDIA_API_KEY` set) |
| `VISION_MODEL` | `nvidia/meta/llama-3.2-11b-vision-instruct` |
| `NVIDIA_PRIMARY_MODEL` | `meta/llama-3.2-11b-vision-instruct` |
| `NVIDIA_FALLBACK_MODELS` | `microsoft/phi-3.5-vision-instruct,microsoft/phi-3-vision-128k-instruct` |
| `MAX_STEPS` | `120` (per survey) |
| `MAX_RETRIES` | `5` |
| `MAX_NO_PROGRESS` | `15` |
| `VISION_CLI_TIMEOUT` | `180` |

### Queue / Orchestration

| Var | Default |
|---|---|
| `HEYPIGGY_DASHBOARD_URL` | `https://www.heypiggy.com/` |
| `HEYPIGGY_SURVEY_URLS` | *empty* — comma-sep list to force specific URLs |
| `HEYPIGGY_MAX_SURVEYS` | `25` |
| `HEYPIGGY_COOLDOWN_SEC` | `4.0` |
| `HEYPIGGY_COOLDOWN_JITTER` | `2.0` |
| `HEYPIGGY_AUTODETECT` | `1` |

### Persona / Profile

| Var | Default |
|---|---|
| `HEYPIGGY_PERSONA` | `default` |
| `HEYPIGGY_PROFILE_PATH` | `$XDG_CONFIG_HOME/heypiggy/profile.json` |
| `HEYPIGGY_PROFILES_DIR` | `$XDG_CONFIG_HOME/heypiggy/profiles/` |
| `HEYPIGGY_PERSONA_ENABLED` | `1` |

### Global Brain (learn-from-failure)

| Var | Default |
|---|---|
| `BRAIN_URL` | `http://127.0.0.1:7070` |
| `BRAIN_PROJECT_ID` | `heypiggy-survey-worker` |
| `BRAIN_AGENT_ID` | `a2a-sin-worker-heypiggy` |
| `BRAIN_ENABLED` | `1` |
| `BRAIN_LOCAL_FALLBACK` | `.pcpm` |

### Media Pipeline

| Var | Default |
|---|---|
| `AUDIO_ASR_MODEL` | `nvidia/parakeet-tdt-0.6b-v2` |
| `VIDEO_UNDERSTANDING_MODEL` | `nvidia/cosmos-reason1-7b` |
| `MEDIA_PIPELINE_ENABLED` | `1` |
| `MEDIA_LANGUAGE` | *(auto)* |

---

## 5. ARCHITECTURE — WHAT LIVES WHERE

```
heypiggy_vision_worker.py     Main loop. dom_prescan() has 21 scanner blocks.
                              This is a ~5800 line file — use Grep, don't read whole.
worker/cli.py                 heypiggy-worker CLI (run, doctor, version).
worker/loop.py                Async run-loop that calls heypiggy_vision_worker.
survey_orchestrator.py        Queue + auto-detect + skip-filter + cooldowns.
panel_overrides.py            PureSpectrum / Dynata / Sapio / Cint / Lucid rules.
platform_profile.py           Pluggable dashboard/panel profile (HeyPiggy default,
                              Prolific/Clickworker/Attapoll/custom JSON via env).
session_store.py              Cross-run cookie+localStorage cache (72h TTL).
bridge_retry.py               Exponential-backoff wrapper for transient MCP calls.
budget_guard.py               Per-run token/request/EUR cap + graceful trip.
persona.py                    User profile → prompt-injectable answer bank.
audio_handler.py              Audio-ASR via NVIDIA Parakeet.
video_handler.py              Video-understanding via NVIDIA Cosmos-Reason.
media_router.py               Decides audio vs video vs image pipeline.
global_brain_client.py        PCPM fact ingest + ask() for DQ skip.
config.py                     Single load_config_from_env() — read this for
                              the authoritative list of every env var.
observability.py              RunSummary (earnings_eur, surveys_*, step_metrics).
state_machine.py              PageState FSM (dashboard/survey_question/done/…).
```

### The 21 `dom_prescan` scanner blocks (hot path on every step)

If you add a new perception feature, it almost certainly goes here:

1. Page-state machine update
2. DOM snapshot + ref IDs
3. Clickable-element ranking
4. Media (audio/video) unlock
5. Question-text + option extraction
6. Screener / DQ-signal detection
7. Obstacle (cookie, translate, modal) killer
8. Dashboard ranking (which survey pays most)
9. Media-unlock fallback (click play overlays)
10. **Matrix / Likert grid** + anti-straight-lining jitter
11. **Slider / range input** (JS value + dispatch change)
12. **Infinite-spinner detection** → auto-reload
13. **Same-question loop detection** → escalation
14. **Required-field validator** (block Weiter if empty)
15. **EUR totalizer** (deduplicated reward aggregation)
16. **Panel override injection** (PureSpectrum/Dynata/Sapio/Cint/Lucid)
17. **Attention-check auto-solver** ("select X" → force click)
18. **Open-ended min-length enforcer** (chars/words demands)
19. **Error-banner recovery** (validation alert → rescan)
20. **Answer-consistency memo** (repeat question → repeat answer)
21. **Quota-full vs DQ discriminator** (bypass brain-learning)

Each block returns a string; all non-empty blocks are joined with `\n\n`
and shoved into the vision prompt.

### Cross-cutting infrastructure
- `execute_bridge()` retries idempotent methods (screenshot/execute_javascript/
  list_tabs/etc) up to 3× with 0.4/0.9/1.8s backoff + jitter. Mutating
  methods (click_ref/type_text/select_option) are NEVER retried.
- `BudgetGuard` (ENV: `BUDGET_MAX_TOKENS`, `BUDGET_MAX_REQUESTS`,
  `BUDGET_MAX_EUR`) tracks NIM token usage and trips cleanly at survey
  boundary — NEVER mid-survey (would be DQ).
- `platform_profile.active()` returns the configured panel. Default is
  HeyPiggy. Override with `A2A_PLATFORM=prolific` / `A2A_PLATFORM=custom`
  + `A2A_PLATFORM_CONFIG=/path/to/profile.json`.
- **Mega-Scan**: Blocks 14/15/16/19 read from a single consolidated JS
  roundtrip (`_mega` dict in `dom_prescan`) instead of 4 separate
  `execute_javascript` calls. Saves ~300-400ms per step
  (~15-20s per survey at 50 steps). If you add a new read-only DOM
  scanner that's cheap to compute, ADD IT TO THE MEGA-SCAN JS
  (not a new bridge call) for maximum throughput.

---

## 6. SESSION PERSISTENCE (DON'T REINVENT)

- On every `save_session(label)`, cookies + localStorage + sessionStorage
  are dumped to `~/.heypiggy/session_cache.json` (mode 0600).
- On startup, `_session_restore()` is called right after the first page
  load, BEFORE any login attempt. If the cache is <72h old it replays
  cookies/storage and reloads the page → we're already logged in.
- To force a fresh login: `rm ~/.heypiggy/session_cache.json`.
- The cache covers `heypiggy.com`, `puresurvey.com`, `dynata.com`,
  `sapiosurveys.com`, `cint.com`, `lucidhq.com` by default. Add more via
  `HEYPIGGY_SESSION_DOMAINS` (comma-sep).

---

## 7. TROUBLESHOOTING DECISION TREE

```
Worker exits with code 2 (config error)
 └─ Run `heypiggy-worker doctor`. Set whatever it complains about.

Worker exits with code 3 (preflight)
 ├─ "bridge_unreachable" → start Bridge MCP first. Check BRIDGE_MCP_URL.
 ├─ "login_failed"       → HEYPIGGY_EMAIL/PASSWORD wrong, OR captcha.
 │                          Delete session_cache.json and retry once.
 └─ "nvidia_auth"        → NVIDIA_API_KEY expired/wrong.

Worker runs but stuck on same page
 ├─ Check logs for "same_question_loop" → block 13 already escalates.
 ├─ Check logs for "required_empty"     → block 14 tells the LLM what's missing.
 ├─ Check logs for "spinner_loop"       → block 12 forces a reload at streak 3.
 └─ If none of the above: the page is a new panel pattern. Add rules to
    panel_overrides.py (see PureSpectrum/Dynata as templates).

Completes surveys but earnings_eur = 0
 └─ Block 15 regex didn't match the reward banner. Check log for the
    actual banner text; extend the regex in dom_prescan's EUR-Totalizer.

Gets disqualified on the same survey every day
 └─ Already handled: DQ is written as a Brain fact; orchestrator's
    should_skip() asks the Brain before opening each URL. Make sure
    BRAIN_ENABLED=1 and BRAIN_URL is reachable (or use the .pcpm local
    fallback — enabled by default).

Panel marks us as "bot"
 ├─ Ensure type_text goes through human-typing (40-180ms jitter per char
 │   — already default).
 ├─ Ensure cooldown between surveys is non-zero (HEYPIGGY_COOLDOWN_SEC).
 └─ Check panel_overrides.py quality_traps for this provider.
```

---

## 8. TESTING

```bash
# Full suite (210+ tests, ~3s)
python -m pytest tests/ -q --ignore=tests/worker

# One module
python -m pytest tests/test_config.py -q

# Compile check (catches syntax errors in the huge worker file fast)
python -m py_compile heypiggy_vision_worker.py persona.py \
    global_brain_client.py survey_orchestrator.py config.py \
    audio_handler.py video_handler.py media_router.py \
    session_store.py panel_overrides.py observability.py
```

**Never** commit with failing tests. If a test blocks you, fix the test
OR the code — don't skip it.

---

## 9. CODE RULES (hard)

1. Python 3.11+, type hints on every public function.
2. No f-string SQL, no string-concatenated selectors — use parameterized
   args via `execute_bridge`.
3. `audit(event, **kv)` for everything observable. NEVER `print()` in
   hot paths.
4. New scanners go into `dom_prescan`, not into the main loop.
5. Panel-specific logic goes into `panel_overrides.py`, not the worker.
6. Persistent learning goes via `GlobalBrainClient.ingest_fact()`.
7. Don't remove imports until you've removed all usages — the linter
   will catch it, but stale imports break imports-order tests.
8. German comments are fine and encouraged (legacy convention); docs
   stay English.

---

## 10. DO-NOT-TOUCH LIST

- `components/ui/*` — shadcn primitives (this is a **Python** repo; the
  few React files are unused legacy from a template).
- `next.config.mjs`, `tailwind.config.ts`, `app/` — same, ignore.
- `user_read_only_context/` — read-only agent scratchpad, never write.
- `v0_memories/skills/` — skills directory, managed by a separate tool.

---

## 11. CONTRACT FOR opencode CLI

When the user invokes:

```
opencode "A2A-SIN-Worker-heypiggy: run"
opencode "start the heypiggy agent"
opencode "earn EUR"
```

The agent MUST:

1. Verify the 3 required env vars (`NVIDIA_API_KEY`, `HEYPIGGY_EMAIL`,
   `HEYPIGGY_PASSWORD`) — if missing, stop and ask.
2. Run `heypiggy-worker doctor` and report its output.
3. If doctor is green, execute `heypiggy-worker run` in the foreground
   (streaming logs). Do not background it.
4. After exit, read `/tmp/heypiggy_<runid>/run_summary.json` and report
   `earnings_eur`, `surveys_completed`, `surveys_disqualified`.
5. If `surveys_disqualified > surveys_completed`, suggest inspecting
   `panel_overrides.py` for the dominant panel this session.

Don't "dry-run", don't suggest rewrites, don't refactor unprompted.
Run the thing, report the numbers, stop.

---

*Last updated to reflect: session_store, panel_overrides,
orchestrator skip-filter, EUR totalizer, required-field validator,
matrix/slider/spinner/loop scanners, human-typing cadence,
native-select handler.*
