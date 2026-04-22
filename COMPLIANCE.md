# COMPLIANCE.md & Responsible Use Guide

## ⚖️ Legal Disclaimer & Liability

`playstealth-cli` is an open-source automation framework provided **"as is"** without warranty of any kind. The authors and contributors assume **no liability** for direct, indirect, incidental, or consequential damages arising from the use, misuse, or inability to use this software. By using this tool, you acknowledge that you operate at your own risk and bear full responsibility for compliance with applicable laws, platform Terms of Service (ToS), and ethical guidelines.

## 🎯 Intended Use Cases

This CLI is designed for:

- ✅ **Authorized Testing**: QA, UX research, and platform compatibility testing with explicit permission.
- ✅ **Educational & Research Purposes**: Studying browser fingerprinting, anti-detection mechanics, and resilient automation patterns.
- ✅ **Personal Automation**: Automating repetitive, non-commercial workflows on platforms that explicitly permit automation.
- ✅ **Diagnostics & Benchmarking**: Validating stealth profiles, DOM resilience, and human-like pacing in controlled environments.

## 🚫 Prohibited Uses

You **must not** use this software for:

- ❌ Violating platform Terms of Service, especially survey/reward platforms that explicitly forbid automation, botting, or multi-accounting.
- ❌ Fraudulent activity, including fake survey submissions, reward farming, identity spoofing, or financial exploitation.
- ❌ Bypassing security controls for malicious purposes (credential stuffing, scraping protected data, evading rate limits for abuse).
- ❌ Running parallel, high-frequency, or 24/7 sessions that mimic non-human behavior or degrade platform services.
- ❌ Distributing, selling, or licensing this tool as a "survey farming" or "money-making" solution.

## 🌐 Platform ToS & Risk Awareness

Most survey and reward platforms explicitly prohibit automated interactions. Detection systems (Cloudflare, DataDome, Akamai, proprietary bot-scorers) continuously evolve. **Automation flags, account suspensions, payout freezes, or IP blacklists are possible consequences.** This tool implements human-like pacing, consistency checks, and graceful degradation to minimize detection risk, but **cannot guarantee undetectability or ToS compliance**. You are solely responsible for verifying platform rules before execution.

## 🔒 Data Privacy & Telemetry

- 📍 **Local-First**: All telemetry (`telemetry.jsonl`), state, manifests, and logs are stored locally in `.playstealth_state/`. Nothing is transmitted externally unless explicitly configured.
- 🕵️ **Anonymized**: Telemetry contains no URLs, question text, answers, IPs, or PII. Only session IDs, step indices, durations, success flags, and error codes.
- 🇪🇺 **GDPR/CCPA Friendly**: No tracking, no cookies, no external analytics. You own your data. Delete `.playstealth_state/` to wipe all traces.
- 🔑 **Secrets Management**: Credentials, API keys, and PEM files are never logged, never committed, and should be managed via Infisical or local env injection.

## 🤝 Responsible Automation Principles

If you choose to automate, follow these rules to maintain ecosystem integrity:

1. **One Session, One Human Rhythm**: Never run parallel surveys. Respect circadian active hours (8–22h default).
2. **Consistency Over Speed**: Maintain stable demographics, reading delays, and natural variance. Straight-lining or speed-runs trigger reviews.
3. **Graceful Exits**: Accept disqualifications. Do not brute-force screening gates or manipulate DOM to bypass eligibility checks.
4. **Rate Limit Respect**: Allow inter-survey breaks (5–25 min). Do not flood endpoints or bypass platform cooldowns.
5. **Transparency & Consent**: Only automate where permitted. When in doubt, assume automation is prohibited.

## 📋 Compliance Checklist

Before running `playstealth`, verify:

- [ ] I have read and understood the target platform's Terms of Service.
- [ ] I am not using this tool for fraudulent, commercial, or unauthorized reward farming.
- [ ] I have configured pacing, breaks, and active hours to mimic human behavior.
- [ ] I store secrets securely (Infisical/local env) and never commit `.env` or PEM files.
- [ ] I accept full responsibility for account status, payouts, and platform enforcement actions.

## 📬 Reporting & Contact

Found a security issue, compliance gap, or ethical concern?  
📧 Email: `security@sin-clis.dev` (PGP key available on request)  
🐛 GitHub Issues: Use `bug` or `compliance` labels. Auto-reported issues are triaged weekly.

---

*Last updated: 2024-06-15 | Maintained by SIN-CLIs | Licensed under MIT*
