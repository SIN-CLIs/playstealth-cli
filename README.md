# A2A-SIN-Worker-heypiggy

Autonomous HeyPiggy survey worker agent for OpenSIN-AI.

## Purpose

Autonomously completes surveys on HeyPiggy platform for monetization.

## Agent Configuration

| Property | Value |
|:---|:---|
| **Team** | Team Worker |
| **Manager** | A2A-SIN-Team-Worker |
| **Type** | Worker Agent |
| **Primary Model** | `google/antigravity-gemini-3-flash` |

### Subagenten-Modelle

| Subagent | Modell |
|:---|:---|
| **explore** | `nvidia-nim/stepfun-ai/step-3.5-flash` |
| **librarian** | `nvidia-nim/stepfun-ai/step-3.5-flash` |

## Agent Config System v5

Registered in central team system:
- **Team Register:** `oh-my-sin.json` → `team-worker`
- **Team Config:** `my-sin-team-worker.json`

→ [Full Documentation](https://github.com/OpenSIN-AI/OpenSIN-documentation/blob/main/docs/guide/agent-configuration.md)

## License

MIT