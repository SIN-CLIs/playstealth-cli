# PlayStealth CLI

Modulare Playwright+Stealth CLI für HeyPiggy-Survey-Flows.

## Zweck

Dieses Repository ist **ausschließlich** für die PlayStealth-CLI gedacht.
Es enthält **keinen** HeyPiggy-Agenten-Worker mehr und ist bewusst als
kleine Tool-Sammlung aufgebaut:

- 1 CLI
- viele kleine Action-Module
- eigene Diagnostics-Tools
- Resume/State/Manifest für robuste Sessions

## Kernbefehle

```bash
playstealth open-list
playstealth click-survey --index 0
playstealth inspect-survey --index 0
playstealth answer-survey --index 0 --option-index 0
playstealth run-survey --index 0 --max-steps 5
playstealth resume-survey --max-steps 5
playstealth tools
playstealth manifest
playstealth state
playstealth diagnose inspect-page
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chrome
```

## Wichtige Umgebungsvariablen

Siehe `.env.example`.

Wichtig:
- Secrets gehören **nicht** in Git.
- Secrets gehören in deine lokale Umgebung oder in Infisical.

## Architektur

- `playstealth_cli.py` — nur Parser/Dispatcher
- `playstealth_actions/` — 1 Tool = 1 Modul
- `playstealth_actions/tool_registry.py` — Tool-Liste
- `playstealth_actions/tool_manifest.py` — JSON-Manifest
- `playstealth_actions/state_store.py` — Persistenter Resume-State
- `playwright_stealth_worker.py` — unterstützende Browser-/Profil-Helfer

## Produktionsstatus

Schon vorhanden:
- Survey-Liste öffnen
- Survey anklicken
- neuen Tab verfolgen
- Consent behandeln
- gängige Fragetypen beantworten
- State speichern und wiederaufnehmen
- Diagnostics-Tools

Noch weiter ausbaubar:
- mehr robuste Selektor-Heuristiken
- mehr Tests pro Fragetyp
- zusätzliche CI-Checks
