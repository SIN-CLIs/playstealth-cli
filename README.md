<a name="readme-top"></a>

<p align="center">
  <img src="https://socialify.git.ci/OpenSIN-AI/A2A-SIN-Worker-heypiggy/image?description=1&font=Inter&forks=1&issues=1&name=1&owner=1&pattern=Circuit%20Board&stargazers=1&theme=Dark" alt="A2A-SIN-Worker-heypiggy" width="960" />
</p>

<p align="center">
  <a href="https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.13+-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  </a>
  <a href="https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/OpenSIN-AI/A2A-SIN-Worker-heypiggy/ci.yml?label=build" alt="Build Status" />
  </a>
  <a href="https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy/issues">
    <img src="https://img.shields.io/github/issues/OpenSIN-AI/A2A-SIN-Worker-heypiggy" alt="Issues" />
  </a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#features">Features</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#fail-learning">Fail Learning</a> ·
  <a href="#observability">Observability</a> ·
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <em>Autonomous HeyPiggy survey worker agent for high-reliability monetization.</em>
</p>

---

> [!NOTE]
> HeyPiggy is part of the **OpenSIN-AI** ecosystem. It autonomously completes surveys on the HeyPiggy platform using vision-guided execution and self-healing logic.

---

## What is HeyPiggy?

HeyPiggy is a **Production-Grade Vision Worker** designed to navigate survey platforms with human-like precision. Unlike traditional scrapers, it uses a multi-layered vision stack (NVIDIA Llama 3.2-11B / Gemini 3 Flash) to understand UI state, bypass blockers, and complete surveys autonomously.

---

## Quick Start

<table>
<tr>
<td width="33%" align="center">
<strong>1. Setup</strong><br/><br/>
<code>git clone OpenSIN-AI/A2A-SIN-Worker-heypiggy</code><br/><br/>
<img src="https://img.shields.io/badge/⏱️_30s-Blue?style=flat" />
</td>
<td width="33%" align="center">
<strong>2. Config</strong><br/><br/>
<code>cp .env.example .env</code><br/>(Add NVIDIA_API_KEY)<br/><br/>
<img src="https://img.shields.io/badge/⏱️_60s-Blue?style=flat" />
</td>
<td width="33%" align="center">
<strong>3. Run</strong><br/><br/>
<code>python heypiggy_vision_worker.py</code><br/><br/>
<img src="https://img.shields.io/badge/⏱️_Go!-Green?style=flat" />
</td>
</tr>
</table>

---

## Features

| Capability | Description | Status |
|:---|:---|:---:|
| **Vision Gate Loop** | Screenshot-driven navigation with DOM verification | ✅ |
| **Fail-Replay Recorder** | 120s Ring-Buffer recorder for post-failure replay | ✅ |
| **NVIDIA Fail-Analysis** | Multi-frame fail analysis via NVIDIA NIM | ✅ |
| **Self-Healing Memory** | Learns from past failures and adapts runtime actions | ✅ |
| **Circuit Breaker** | Protection against API overload and usage limits | ✅ |
| **Typed Config** | Unified configuration with environment overrides | ✅ |

---

## Architecture

```mermaid
flowchart TB
    subgraph Platform["HeyPiggy.com"]
        direction LR
        UI["Survey UI"]
        Bridge["OpenSIN Bridge MCP"]
    end

    subgraph Core["Worker Engine"]
        direction TB
        Loop["Vision Gate Loop"]
        Config["Typed WorkerConfig"]
        Breaker["Circuit Breaker"]
        State["State Machine"]
    end

    subgraph AI["Vision Stack"]
        direction LR
        NV["NVIDIA Llama 3.2-11B (Primary)"]
        Gemini["Gemini 3 Flash (Fallback)"]
    end

    subgraph Memory["Reliability Layer"]
        Rec["Ring-Buffer Recorder"]
        Anal["Fail Analyzer"]
        Learn["Fail-Learning Memory"]
    end

    UI <--> Bridge
    Bridge <--> Loop
    Loop --> AI
    Loop --> State
    AI --> Loop
    Rec --> Loop
    Loop -- failure --> Anal
    Anal --> Learn
    Learn -- context --> Loop
```

---

## Fail Learning

HeyPiggy doesn't just crash — it learns. Every time a run exits in a failure state:
1. **Keyframes** are extracted from the ring-buffer recorder.
2. **NVIDIA Llama-90B** performs a multi-frame root cause analysis.
3. **Denylists** are updated with bad selectors or action signatures.
4. **Adaptive Delays** are applied to the next run to avoid timing races.

---

## Observability

Every run emits a structured `run_summary.json` including:
- **Step Metrics:** Verdicts, durations, and action success rates.
- **Timing Data:** Average vision and bridge call times.
- **Fail Context:** Terminal exit reasons and final page state.
- **Circuit Status:** Monitoring snapshots of the NVIDIA NIM backend.

---

## Configuration

The worker can be configured via environment variables:

<details>
<summary>View Environment Variables</summary>

| Variable | Default | Description |
|:---|:---|:---|
| `NVIDIA_API_KEY` | (required) | Your NVIDIA NIM API Key |
| `VISION_BACKEND` | `auto` | `auto`, `nvidia`, or `opencode` |
| `MAX_STEPS` | `120` | Maximum steps per run |
| `MAX_RETRIES` | `5` | Retries per vision step |
| `HEYPIGGY_EMAIL` | (required) | Platform login email |
| `HEYPIGGY_PASSWORD` | (required) | Platform login password |

</details>

---

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for more information.

---

<p align="center">
  <a href="https://opensin.ai">
    <img src="https://img.shields.io/badge/🤖_Powered_by-OpenSIN--AI-7B3FE4?style=for-the-badge&logo=github&logoColor=white" alt="Powered by OpenSIN-AI" />
  </a>
</p>

<p align="center">
  <sub>Entwickelt vom <a href="https://opensin.ai"><strong>OpenSIN-AI</strong></a> Ökosystem – Enterprise AI Agents die autonom arbeiten.</sub><br/>
  <sub>🌐 <a href="https://opensin.ai">opensin.ai</a> · 💬 <a href="https://opensin.ai/agents">Alle Agenten</a> · 🚀 <a href="https://opensin.ai/dashboard">Dashboard</a></sub>
</p>

<p align="right">(<a href="#readme-top">back to top</a>)</p>
