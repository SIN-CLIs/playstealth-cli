# PlayStealth Hacking Guide

## 🎯 Ziel: Die "weltbeste CLI für Hacker"

PlayStealth ist nicht nur ein Survey-Automation-Tool – es ist eine Plattform für **Anti-Detection Engineering** und **resiliente Automatisierung**. Dieser Guide erklärt, wie du das Maximum aus der Stealth-Engine herausholst.

---

## 📋 Inhaltsverzeichnis

1. [Architektur-Überblick](#architektur-überblick)
2. [Stealth-System verstehen](#stealth-system-verstehen)
3. [Human Behavior Engine](#human-behavior-engine)
4. [Robuste Selektor-Heuristiken](#robuste-selektor-heuristiken)
5. [Session & State Management](#session--state-management)
6. [Diagnostik & Leak-Tests](#diagnostik--leak-tests)
7. [Neue Survey-Typen hinzufügen](#neue-survey-typen-hinzufügen)
8. [Best Practices für Production](#best-practices-für-production)

---

## Architektur-Überblick

```
playstealth_cli.py          # Haupt-CLI Entry Point
├── playstealth_actions/
│   ├── human_behavior.py   # Human-like mouse/keyboard simulation
│   ├── stealth_enhancer.py # Anti-detection JS injections
│   ├── smart_actions.py    # Multi-strategy selector resolution
│   ├── tool_registry.py    # Tool definitions
│   ├── diagnostic_runner.py # Diagnostics execution
│   └── ...                 # Question handlers
└── playwright_stealth_worker.py # Low-level browser worker
```

### Kernprinzipien

1. **Defense in Depth**: Mehrere Stealth-Schichten (Browser-Level + JS-Injections)
2. **Graceful Degradation**: Fallback-Strategien bei jedem Schritt
3. **State Persistence**: Crash-resistent durch JSON-State-Snapshots
4. **Modularität**: Jedes Tool ist unabhängig testbar

---

## Stealth-System verstehen

### Schicht 1: Browser-Konfiguration

```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    context = await p.chromium.launch_persistent_context(
        user_data_dir="/path/to/profile",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
    )
```

### Schicht 2: playwright-stealth

```python
from playwright_stealth.stealth import Stealth

stealth = Stealth()
await stealth.apply_stealth_async(page)
```

### Schicht 3: Advanced Injections (`stealth_enhancer.py`)

Die mächtigste Schicht – custom JS-Injections die spezifische Fingerprints manipulieren:

```python
from playstealth_actions.stealth_enhancer import inject_advanced_stealth

await inject_advanced_stealth(page, config={
    "session_seed": "unique-per-session-id"
})
```

**Was wird manipuliert?**

| Property | Manipulation | Zweck |
|----------|--------------|-------|
| `navigator.webdriver` | `undefined` | Versteckt Automation |
| WebGL Vendor/Renderer | Realistische GPU-Werte | Verhindert SwiftShader-Detection |
| Canvas | Subtiles Rauschen | Verhindert deterministische Fingerprints |
| AudioContext | Mikro-Variationen | Verhindert Audio-Fingerprinting |
| `navigator.plugins` | Mock-Plugins | Simuliert echte Browser-Umgebung |
| `navigator.languages` | Konsistente Liste | Passt zu User-Agent |
| Permissions API | "prompt" statt "denied" | Versteckt Headless-Indikator |
| Screen Dimensions | outer !== inner | Verhindert Headless-Erkennung |

### Session-Konsistenz

Wichtig: Alle randomisierten Werte werden aus einem **Session-Seed** abgeleitet. Das bedeutet:
- Innerhalb einer Session sind Fingerprints konsistent
- Zwischen Sessions variieren sie natürlich

```python
import hashlib
session_hash = hashlib.md5("my-seed".encode()).hexdigest()
webgl_index = int(session_hash[:2], 16) % len(VENDORS)
```

---

## Human Behavior Engine

Die `human_behavior.py` Module simuliert **menschliche Ungenauigkeit**:

### Bézier-Mausbewegungen

Statt gerader Linien:
```python
from playstealth_actions.human_behavior import mouse_move_curve

await mouse_move_curve(page, target_x=500, target_y=300)
```

Das erzeugt S-Kurven mit:
- Zufälligen Kontrollpunkten
- Mikroskopischem Jitter (±0.5px)
- Variabler Geschwindigkeit

### Gaussian Delays

Keine festen `sleep(2)` – stattdessen:
```python
from playstealth_actions.human_behavior import gaussian_delay

await asyncio.sleep(gaussian_delay(mean=0.5, std=0.15))
# Ergebnis z.B.: 0.42s, 0.61s, 0.38s...
```

### Human Click

Ersetzt `page.click()` komplett:
```python
from playstealth_actions.human_behavior import human_click

success = await human_click(page, "button.submit")
```

**Ablauf:**
1. Element scrollen + warten (200ms ±)
2. Maus mit Bézier-Kurve bewegen (~500px/s)
3. Pre-Click Hesitation (300ms ±)
4. Mouse Down (50-150ms Hold)
5. Mouse Up
6. Post-Click Reaction (200ms ±)

### Idle Time Simulation

Menschen lesen! Simuliere Lesezeit:
```python
from playstealth_actions.human_behavior import idle_time

await idle_time(page, mean_duration=2.0, std=0.8)
# Währenddessen: subtile Mausbewegungen, Mini-Scrolls
```

---

## Robuste Selektor-Heuristiken

CSS-Selektoren brechen bei DOM-Änderungen. Die `smart_actions.py` verwendet **Multi-Strategy Resolution**:

### Smart Click

```python
from playstealth_actions.smart_actions import smart_click

result = await smart_click(page, "Weiter")
# result = {"success": True, "action": "click", "target": "Weiter"}
```

**Strategien (in Reihenfolge):**

1. **Aria-Label Exact**: `[aria-label="Weiter"]`
2. **Data-TestID**: `[data-testid="Weiter"]`
3. **Button Text Exact**: `button:has-text("Weiter")`
4. **Button Text Contains**: Iteriert alle Buttons, fuzzy match
5. **Link Text**: Sucht in `<a>` Tags
6. **Input Value/Placeholder**: Für Submit-Buttons
7. **Role Attribute**: `[role="button"]:has-text(...)`
8. **Onclick Handler**: `<div onclick="...">` mit Textinhalt

### Smart Type

```python
from playstealth_actions.smart_actions import smart_type

await smart_type(page, "E-Mail", "user@example.com")
```

**Strategien:**
1. Label-Text → findet assoziiertes Input via `for` Attribut
2. Placeholder-Text Match
3. Name-Attribute (partial match)
4. ID-Attribute (partial match)
5. Aria-Label Match

### Eigene Strategien hinzufügen

```python
class SmartCustomAction:
    def __init__(self, page: Page):
        self.page = page
    
    async def execute(self, target: str) -> bool:
        # Strategie 1: ...
        # Strategie 2: ...
        return False
```

---

## Session & State Management

### State Persistenz

Der State wird automatisch nach jedem Schritt gespeichert:
```json
{
  "url": "https://www.heypiggy.com/dashboard",
  "step": 5,
  "survey_id": "abc123",
  "question_count": 12
}
```

### Browser Context Persistenz

Für echtes Resume muss der **gesamte Browser-Kontext** gespeichert werden:

```python
# Speichern
await context.storage_state(path="/tmp/session.json")

# Laden
context = await browser.new_context(storage_state="/tmp/session.json")
```

**Gespeichert werden:**
- Cookies (inkl. HttpOnly)
- LocalStorage
- SessionStorage
- IndexedDB (optional)

### Resume Implementierung

```python
from playstealth_actions.resume_survey import run

exit_code = await run(timeout_seconds=300, max_steps=20)
```

---

## Diagnostik & Leak-Tests

### Integrierte Diagnostic Tools

```bash
# Alle verfügbaren Tools anzeigen
playstealth diagnose --help

# Comprehensive Stealth Check
playstealth diagnose check-stealth

# Nur WebGL prüfen
playstealth diagnose check-webgl

# Timezone/Locale prüfen
playstealth diagnose check-timezone

# Headless Indikatoren
playstealth diagnose check-headless
```

### Beispiel-Output: `check-stealth`

```
=== Comprehensive Stealth Check ===

Navigator Properties:
  User Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36...
  WebDriver: None
  Languages: ['en-US', 'en', 'de-DE', 'de']
  Platform: MacIntel
  Hardware Concurrency: 8
  Device Memory: 8 GB
  Plugins: 3

Leak Detection Results:
  WebDriver Property: ✅ CLEAN
  Headless Indicators: ✅ CLEAN
  WebGL Fingerprint: ✅ CLEAN
  Canvas Fingerprint: ✅ CLEAN

✅ GOOD: Basic stealth properties look natural.

Note: For production use, also test against:
  - https://bot.sannysoft.com/
  - https://abrahamjuliot.github.io/creepjs/
  - https://pixelscan.net/
```

### Externe Test-Services

**Vor Production-Einsatz testen:**

1. **[SannySoft Bot Detection](https://bot.sannysoft.com/)**
   - Prüft grundlegende Headless-Indikatoren
   - Ziel: Alle grünen Häkchen

2. **[CreepJS](https://abrahamjuliot.github.io/creepjs/)**
   - Detaillierter Fingerprint-Vergleich
   - Zeigt "Trust Score"
   - Ziel: < 20% Abweichung von normalen Usern

3. **[PixelScan](https://pixelscan.net/)**
   - Kombiniert multiple Detection-Techniken
   - Ziel: "Consistent" Status ohne Warnungen

---

## Neue Survey-Typen hinzufügen

### Schritt 1: Question Type erkennen

In `detect_question_type.py` erweitern:

```python
async def detect_question_type(page) -> dict:
    # ... existierende Checks ...
    
    # Neuer Typ: Rating Scale
    rating_elements = await page.query_selector_all('.rating-scale input[type="radio"]')
    if len(rating_elements) >= 5:
        return {"question_type": "rating", ...}
```

### Schritt 2: Handler implementieren

Neue Datei `playstealth_actions/rating_question.py`:

```python
"""Handler for rating scale questions."""

from playwright.async_api import Page
from playstealth_actions.human_behavior import human_click

async def handle_rating(page: Page, strategy: str = "middle") -> bool:
    """
    Handle rating scale (1-5 stars, Likert, etc.)
    
    Args:
        page: Playwright page
        strategy: "positive", "neutral", "negative", "random"
    """
    rating_inputs = await page.query_selector_all(
        '.rating-scale input[type="radio"], .stars input[type="radio"]'
    )
    
    if not rating_inputs:
        return False
    
    # Strategie-basierte Auswahl
    if strategy == "positive":
        index = len(rating_inputs) - 1  # Höchste Bewertung
    elif strategy == "neutral":
        index = len(rating_inputs) // 2
    elif strategy == "random":
        import random
        index = random.randint(0, len(rating_inputs) - 1)
    else:
        index = 0
    
    # Human-like click
    selector = f"input[type='radio']:nth-of-type({index + 1})"
    return await human_click(page, selector)
```

### Schritt 3: Router aktualisieren

In `question_router.py` den neuen Typ registrieren:

```python
from playstealth_actions.rating_question import handle_rating

QUESTION_HANDLERS = {
    "radio": handle_radio,
    "checkbox": handle_checkbox,
    "rating": handle_rating,  # Neu!
    # ...
}
```

### Schritt 4: Tool Registry updaten

In `tool_registry.py` hinzufügen:

```python
ToolSpec(
    "rating-question",
    "playstealth_actions.rating_question",
    "Handle rating scale questions",
    "implemented",
),
```

---

## Best Practices für Production

### 1. Proxy-Rotation

```python
# In browser_bootstrap.py oder ähnlich
proxy_config = {
    "server": "http://proxy-provider.com:8080",
    "username": "user",
    "password": "pass",
}

context = await browser.new_context(proxy=proxy_config)
```

**Wichtig:** Timezone muss zur Proxy-IP passen!
```python
from playstealth_actions.stealth_enhancer import apply_timezone_spoof

await apply_timezone_spoof(page, "America/New_York")  # Für US-East-Proxies
```

### 2. User-Agent Rotation

```python
from playstealth_actions.stealth_enhancer import generate_user_agent

ua = generate_user_agent(os_type="windows")
# Setzen via Browser-Args oder JS-Injection
```

### 3. Rate Limiting

Nicht zu schnell agieren:
```python
# Zwischen Aktionen
from playstealth_actions.human_behavior import idle_time

await idle_time(page, mean_duration=3.0, std=1.0)  # 2-4 Sekunden Pause
```

### 4. Error Handling mit Retry

```python
async def robust_action(page, selector, max_retries=3):
    for attempt in range(max_retries):
        try:
            success = await human_click(page, selector)
            if success:
                return True
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            
        # Backoff mit Jitter
        await asyncio.sleep(1.0 * (attempt + 1) + random.uniform(0, 0.5))
    
    return False
```

### 5. Logging ohne Secrets

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Gut:
logger.info(f"Clicked button: {selector}")

# Schlecht (vermeiden!):
logger.info(f"Filled email: {email}")  # Could contain PII
```

### 6. Testing gegen reale Targets

Bevor du in Production gehst:

1. **Lokale Tests**: `pytest tests/`
2. **Staging-Umgebung**: Teste gegen Test-Survey-Instanzen
3. **Canary Runs**: Erst 1-2 Umfragen, dann skalieren
4. **Monitoring**: Logs auf Errors überwachen

---

## Troubleshooting

### Problem: Seite erkennt Bot trotzdem

**Lösung:**
1. `playstealth diagnose check-stealth` ausführen
2. Gegen CreepJS testen
3. Proxy-Timezone Mismatch prüfen
4. Canvas/WebGL Injections verifizieren

### Problem: Selektoren brechen häufig

**Lösung:**
1. Smart Actions verwenden (nicht direkte CSS-Selektoren)
2. Data-Attribute priorisieren (`data-testid`, `data-cy`)
3. Text-basierte Fallbacks implementieren
4. Screenshots bei Failure für Debug

### Problem: Session geht nach Reload verloren

**Lösung:**
1. `context.storage_state()` vor jedem wichtigen Schritt speichern
2. Beim Resume vollständigen Kontext laden
3. LocalStorage manuell persistieren falls nötig

---

## Contributing

### Pull Request Checklist

- [ ] Neue Funktionen haben Tests
- [ ] Stealth-Injections sind dokumentiert
- [ ] Keine Hardcoded Secrets
- [ ] Logging ist angemessen
- [ ] HACKING.md wurde aktualisiert

### Code Style

- Type hints für alle öffentlichen Funktionen
- Docstrings im Google-Style
- Async/Await konsequent verwenden

---

## Ressourcen

- [Playwright Documentation](https://playwright.dev/python/)
- [playwright-stealth GitHub](https://github.com/AtuboDad/playwright-stealth)
- [Bot Detection Wiki](https://github.com/kaliiiiiiiiii/bot-detector)
- [CreepJS Source](https://github.com/AbrahamJuliot/creepjs)

---

**Viel Erfolg beim Hacken! 🚀**

Denk dran: Mit großer Automatisierungs-Power kommt große Verantwortung. Nutze PlayStealth ethisch und respektiere immer die Nutzungsbedingungen der Zielseiten.
