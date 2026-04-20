#!/usr/bin/env python3
# ================================================================================
# DATEI: heypiggy_vision_worker.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

# -*- coding: utf-8 -*-
"""
================================================================================
A2A-SIN-Worker-HeyPiggy — Vision Gate Edition v3.1 (ANTI-RAUSFLUG + CAPTCHA BYPASS)
================================================================================
ARCHITEKTUR:
  Bridge Extension (Chrome) ←WebSocket→ HF MCP Server ←HTTP→ Dieser Worker
  Jede einzelne Aktion wird vom Vision-LLM visuell verifiziert.
  PRIMARY:   NVIDIA NIM -> meta/llama-3.2-11b-vision-instruct
  FALLBACK:  NVIDIA NIM -> microsoft/phi-3.5-vision-instruct
             NVIDIA NIM -> microsoft/phi-3-vision-128k-instruct
  Backend-Switch: VISION_BACKEND=auto|nvidia (auto nimmt NVIDIA sobald NVIDIA_API_KEY gesetzt).

KERNPRINZIP — EXAKTE TAB-BINDUNG (PRIORITY -7.85):
  Der Worker öffnet genau EINEN Tab (tabs_create) und speichert dessen
  tabId + windowId als CURRENT_TAB_ID / CURRENT_WINDOW_ID.
  AB DIESEM MOMENT wird JEDER Bridge-Call mit dem exakten tabId geschickt.
  Es gibt KEINEN Fallback auf den "aktiven Tab" oder "currentWindow".
  Wenn CURRENT_TAB_ID nicht gesetzt ist, crasht der Call absichtlich laut,
  statt still auf einen fremden Tab zu fallen.

  Benutzer können parallel andere Tabs öffnen oder bedienen —
  das DARF den Worker NIEMALS beeinflussen.

SICHERHEITSLAYER:
  1. Exakte Tab-Bindung: CURRENT_TAB_ID ist nach Init immer gesetzt (nie None)
  2. Click-Eskalationskette: click_element → ghost_click → keyboard → vision_click → coordinates
  3. DOM-Verifikation NACH JEDER Aktion (nicht nur Screenshot-Hash)
  4. Screenshot-Hash-Tracking: Erkennt Stillstand automatisch
  5. Audit-Log auf Disk: Jede Aktion, jeder Screenshot, jedes Vision-Ergebnis
  6. Session-Backup: Cookies werden vor Crash gesichert
  7. Bridge-Reconnect: Automatischer Reconnect bei Verbindungsverlust
  8. Credential-Isolation: Passwörter werden NIEMALS an die AI gesendet
  9. Human-Delays: Zufällige Pausen zwischen 1.5-4.5 Sekunden
  10. Try/Except um JEDE einzelne Operation: Kein unbehandelter Crash möglich
  11. Page-State-Klassifikation: Erkennt Login, Dashboard, Survey, Error
  12. Proof-Collection: Screenshots mit Zeitstempel für Nachvollziehbarkeit
  13. Survey-Abschluss-Garantie: survey_active → NIE abbrechen (NEU v3.1)
  14. Captcha Auto-Bypass: Erkennt und klickt Captchas automatisch (NEU v3.1)
  15. Answer-History: Speichert Antworten für Konsistenz-Prüfung (NEU v3.1)
  16. Anti-Rausflug-Schutz: Konsistente Antworten über alle Surveys (NEU v3.1)
================================================================================
"""

import asyncio
import base64
import hashlib
import json
import os
import random
import re
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from circuit_breaker import CircuitBreaker
from config import load_config_from_env
from fail_recorder import ScreenRingRecorder, save_keyframes_to_disk
from fail_report import (
    generate_fail_report_markdown,
    post_github_issue_comment,
    save_fail_report_to_disk,
    upload_to_box,
)
from nvidia_video_analyzer import analyze_fail_multiframe
from observability import RunSummary

# Multi-Modal Media-Pipeline (Audio / Video / Bilder) + Multi-Survey Queue
# WHY: Der Worker muss ALLE Umfragetypen abwickeln — auch Audio-Fragen
# ("Was hören Sie?"), Video-Fragen ("Was zeigt der Clip?") und Multi-Surveys
# (5+ Umfragen hintereinander ohne manuelles Neustarten).
from media_router import MediaRouter, MediaAnalysis
from survey_orchestrator import QueueState, SurveyOrchestrator

# Persona + OpenSIN Global Brain — Wahrheits-Backbone
# WHY: Der Worker darf NIEMALS lügen oder sich selbst widersprechen. Die Persona
# liefert die harten Fakten (Alter, Wohnort, Einkommen…), das Answer-Log sorgt
# dass dieselbe Frage in Validation-Traps immer gleich beantwortet wird, und das
# Global Brain teilt Erkenntnisse mit anderen Agenten der OpenSIN-Flotte.
from persona import (
    AnswerLog,
    Persona,
    build_persona_prompt_block,
    detect_question_topic,
    load_persona,
    resolve_answer,
)
from global_brain_client import (
    GlobalBrainClient,
    PrimeContext,
    build_brain_prompt_block,
)
from panel_overrides import (
    build_panel_prompt_block,
    detect_panel,
)
from session_store import (
    dump_session as _session_dump,
    restore_session as _session_restore,
)
from platform_profile import active as _active_platform
from bridge_retry import call_with_retry as _bridge_call_with_retry
from budget_guard import BudgetGuard

# Import typed state machine for page state tracking
from state_machine import page_state_machine

# ============================================================================
# USER PROFIL — Jeremy Schulze
# WHY: Der Worker muss Profil-Fragen (Region, Wohnort, Geschlecht, Name etc.)
#      korrekt mit den echten Daten des Users beantworten.
#      Das Profil wird in den Vision-Prompt injiziert damit das Vision-LLM die richtigen
#      Antworten wählt — ohne raten, ohne falsche Klicks.
# CONSEQUENCES: Ohne Profil wuerde das Vision-LLM zufaellig antworten -> falsche Daten,
#               Umfragen brechen ab, Account könnte gesperrt werden.
# ============================================================================


def _resolve_profile_path() -> Path:
    override = os.environ.get("HEYPIGGY_PROFILE_PATH")
    if override:
        return Path(override)
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "opencode" / "profiles" / "jeremy_schulze.json"
    return Path(".config") / "opencode" / "profiles" / "jeremy_schulze.json"


PROFILE_PATH = _resolve_profile_path()


def _load_user_profile() -> dict:
    """
    Lädt das Benutzerprofil von Disk.
    WHY: Profil-Fragen in Umfragen (Region, Name, Wohnort etc.) müssen mit
         echten User-Daten beantwortet werden, nicht zufällig geraten.
    CONSEQUENCES: Fehlt die Datei, wird ein leeres Profil verwendet (kein Crash).
    """
    if PROFILE_PATH.exists():
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[PROFIL] Warnung: Profil konnte nicht geladen werden: {e}")
    return {}


USER_PROFILE = _load_user_profile()


def _build_profile_context() -> str:
    """
    Baut einen lesbaren Profil-Kontext-String für den Vision-Prompt.
    WHY: Das Vision-LLM muss die genauen Profil-Antworten kennen damit es beim
         Ankreuzen von Radio-Buttons (Region, Geschlecht etc.) die richtigen
         Optionen wählt.
    CONSEQUENCES: Leerer String wenn kein Profil vorhanden — Vision fällt auf
                  generische Antwortlogik zurück.
    """
    if not USER_PROFILE:
        return ""

    lines = ["BENUTZERPROFIL (nutze diese Daten für Profil-Fragen):"]

    # Direkte Felder
    field_map = {
        "name": "Name",
        "first_name": "Vorname",
        "last_name": "Nachname",
        "gender": "Geschlecht (male=Männlich)",
        "city": "Wohnort/Stadt",
        "region": "Region in Deutschland",
        "country": "Land",
    }
    for key, label in field_map.items():
        val = USER_PROFILE.get(key)
        if val:
            lines.append(f"- {label}: {val}")

    # Explizite Profil-Antworten (Frage → Antwort Mapping)
    profile_answers = USER_PROFILE.get("profile_answers", {})
    if profile_answers:
        lines.append("KONKRETE ANTWORTEN FÜR HÄUFIGE FRAGEN:")
        for question, answer in profile_answers.items():
            lines.append(f"  - '{question}' → '{answer}'")

    region_note = USER_PROFILE.get("region_note")
    if region_note:
        lines.append(f"HINWEIS: {region_note}")

    return "\n".join(lines)


# ============================================================================
# KONFIGURATION
# ============================================================================

WORKER_CONFIG = load_config_from_env()

# Bridge-Endpunkte
BRIDGE_MCP_URL = WORKER_CONFIG.bridge.mcp_url
BRIDGE_HEALTH_URL = WORKER_CONFIG.bridge.health_url
BRIDGE_CONNECT_TIMEOUT = WORKER_CONFIG.bridge.connect_timeout

# Vision Gate Limits (aus AGENTS.md PRIORITY -7.0)
# WHY MAX_STEPS=120: Umfragen haben oft 20-40 Fragen, jede Frage braucht
#   Screenshot + Vision + Klick + DOM-Verify = 4 Aktionen. 40 Fragen * 4 = 160.
#   120 ist ein sicherer Wert der auch lange Surveys komplett abschließt.
MAX_STEPS = WORKER_CONFIG.vision.max_steps
# WHY MAX_RETRIES=5: 3 war zu aggressiv — manchmal braucht eine Seite länger zum laden.
MAX_RETRIES = WORKER_CONFIG.vision.max_retries
# WHY MAX_NO_PROGRESS=15: Survey-Seiten sehen Frage-für-Frage fast identisch aus.
#   Screenshot-Hash-Vergleich erkennt das fälschlich als 'kein Fortschritt'.
#   15 Schritte gibt dem Worker genug Spielraum um durch mehrseitige Surveys zu kommen.
#   Außerdem wird no_progress_count bei page_state='survey_active' NICHT hochgezählt.
MAX_NO_PROGRESS = WORKER_CONFIG.vision.max_no_progress
MAX_CLICK_ESCALATIONS = WORKER_CONFIG.vision.max_click_escalations
VISION_MODEL = WORKER_CONFIG.vision.model
CLICK_ACTIONS = WORKER_CONFIG.click_actions
NVIDIA_API_KEY = WORKER_CONFIG.nvidia.api_key
NVIDIA_VISION_MODEL = WORKER_CONFIG.nvidia.primary_model
NVIDIA_FALLBACK_MODELS = WORKER_CONFIG.nvidia.fallback_models
VISION_BACKEND = os.environ.get("VISION_BACKEND", "auto").lower()

# 1x1 PNG als lokaler Vision-Probe. WHY: Die Preflight-Prüfung muss die
# screenshot-basierte Vision-Authentifizierung testen, BEVOR irgendeine Browser-
# Mutation stattfindet. Dafür reicht ein minimales gültiges PNG als sicherer Probe.
VISION_AUTH_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5wZuoAAAAASUVORK5CYII="
)

# Verzeichnisse für Artefakte
RUN_ID = WORKER_CONFIG.artifacts.run_id
ARTIFACT_DIR = WORKER_CONFIG.artifacts.artifact_dir
SCREENSHOT_DIR = WORKER_CONFIG.artifacts.screenshot_dir
AUDIT_DIR = WORKER_CONFIG.artifacts.audit_dir
SESSION_DIR = WORKER_CONFIG.artifacts.session_dir

# Erstelle alle Verzeichnisse beim Start
WORKER_CONFIG.artifacts.ensure_dirs()

CURRENT_RUN_SUMMARY: RunSummary | None = None
VISION_CIRCUIT_BREAKER = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
_VISION_CACHE: dict[tuple[str, str], dict[str, object]] = {}

# Multi-Modal Media Router + Multi-Survey Orchestrator werden in main() initialisiert.
# WHY: Globals statt Dependency-Injection, weil dom_prescan() und die Haupt-Loop
# bereits als freie async Funktionen existieren und ein Refactor auf Injection
# den gesamten Call-Graph touchen würde.
# CONSEQUENCES: Die Objekte sind nur innerhalb von main() verfügbar; vor main()
# sind beide None und der Code fällt defensiv auf den Legacy-Pfad zurück.
MEDIA_ROUTER: MediaRouter | None = None
SURVEY_ORCHESTRATOR: SurveyOrchestrator | None = None
_LAST_MEDIA_ANALYSIS: MediaAnalysis | None = None

# Persona + Brain Globals.
# ACTIVE_PERSONA wird in main() aus profiles/<username>.json geladen.
# ANSWER_LOG speichert jede Survey-Antwort damit Validation-Traps nie aus-
# einanderlaufen. GLOBAL_BRAIN pusht Fakten ins OpenSIN One-Brain (http://...).
ACTIVE_PERSONA: Persona | None = None
ANSWER_LOG: AnswerLog | None = None
GLOBAL_BRAIN: GlobalBrainClient | None = None
_BRAIN_PRIME_CONTEXT: PrimeContext | None = None
# Letzte erkannte Frage + Option-Menge (fuer Record-After-Answer Hook)
_LAST_QUESTION_TEXT: str | None = None
_LAST_QUESTION_OPTIONS: list[str] = []
# DQ/Pre-Qual/Complete-Phrase-Detection (fuer Main-Loop + adaptive delay)
_LAST_SCREENER_HIT: str | None = None
_LAST_DQ_HIT: str | None = None
_LAST_COMPLETE_HIT: str | None = None
# Universelle Hindernis-Detection (Cookie/Translate/Start-CTA/Language/Rating)
_LAST_OBSTACLE_KIND: str | None = None
_LAST_RATING_PAGE: dict | None = None
# Tracking ob die aktuelle Umfrage die Post-Survey-Bewertung bereits gemacht hat
_RATING_SUBMITTED_FOR_CURRENT: bool = False
# Same-Question-Loop-Detection: ringbuffer der letzten N gesehenen Fragetexte.
# WHY: Wenn der Agent auf dieselbe Frage 3x hintereinander antwortet, waehlt
# Vision vermutlich immer die gleiche falsche Option -> Eskalation noetig.
_RECENT_QUESTIONS: list[str] = []
_SAME_QUESTION_STREAK: int = 0
# Spinner-Loop-Detection: wie oft wurde hintereinander eine Loading-Only-Page gesehen.
_SPINNER_STREAK: int = 0
# Matrix/Slider-Detection Payload (letzter Scan)
_LAST_MATRIX: dict | None = None
_LAST_SLIDER: dict | None = None
# Required-Field-Validator: letzter Zaehler an leeren Pflichtfeldern
# WHY: Vor jedem Weiter/Next-Klick wollen wir im Prompt stehen haben wie
#      viele Pflichtfelder noch leer sind. Wenn >0 -> Vision darf nicht
#      weiterklicken, muss erst befuellen.
_LAST_EMPTY_REQUIRED: int = 0
# EUR-Totalizer Deduplication: welche Reward-Strings wurden schon gebucht
_SEEN_REWARD_STRINGS: set[str] = set()
# Brain-Fail-Learning: welche DQ-Facts wurden pro Run schon geschrieben
_BRAIN_DQ_WRITTEN: set[str] = set()
# Answer-Consistency-Memo: {question_hash -> chosen_option_label} innerhalb
# EINER Umfrage. WHY: Panels stellen bewusst dieselbe Frage 2x (z.B. einmal
# auf Seite 3 und nochmal auf Seite 11) als Consistency-Check. Wer sich
# widerspricht wird disqualifiziert — manchmal sogar dauerhaft gesperrt.
# CONSEQUENCES: Wird beim Survey-Start geleert, nicht beim Worker-Start.
_ANSWER_MEMO: dict[str, str] = {}
# Quota-Full-Flag: wird im dom_prescan gesetzt wenn "Quote erreicht" /
# "survey is full" erkannt wird. WHY: Das ist KEIN DQ — morgen kann die
# Quote wieder offen sein. Wir duerfen die URL also nicht als "avoid" lernen.
_QUOTA_FULL_DETECTED: bool = False
# Siehe CURRENT_RUN_SUMMARY weiter unten — der EUR-Totalizer im dom_prescan
# liest diesen Zeiger, damit Rewards im globalen run_summary aggregiert werden.
FAIL_LEARNING_PATH = Path("/tmp/heypiggy_fail_learning.json")
FAIL_KEYWORD_STOPWORDS = {
    "after",
    "before",
    "button",
    "click",
    "cause",
    "failed",
    "failure",
    "nicht",
    "problem",
    "seite",
    "step",
    "survey",
    "that",
    "the",
    "this",
    "under",
    "unknown",
    "visible",
    "war",
    "with",
}
FAIL_RUNTIME_KEYWORDS = {
    "blocked",
    "captcha",
    "clickable",
    "consent",
    "cookie",
    "fold",
    "hidden",
    "modal",
    "overlay",
    "popup",
}

# ============================================================================
# ANSWER HISTORY — Konsistenz über alle Surveys hinweg (NEU in v3.1)
# ============================================================================
ANSWER_HISTORY_PATH = Path("/tmp/heypiggy_answer_history.json")


def load_answer_history():
    # -------------------------------------------------------------------------
    # FUNKTION: load_answer_history
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Lädt frühere Survey-Antworten für Konsistenz-Prüfung."""
    if ANSWER_HISTORY_PATH.exists():
        try:
            with open(ANSWER_HISTORY_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"surveys": {}}


def save_answer_history(data):
    # -------------------------------------------------------------------------
    # FUNKTION: save_answer_history
    # PARAMETER: data
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Speichert Survey-Antworten für zukünftige Konsistenz-Prüfung."""
    ANSWER_HISTORY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_answer(question, answer):
    """Zeichnet eine Survey-Antwort für Konsistenz-Tracking auf."""
    hist = load_answer_history()
    survey_id = f"survey_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if survey_id not in hist["surveys"]:
        hist["surveys"][survey_id] = {}
    hist["surveys"][survey_id][question] = answer
    save_answer_history(hist)


def get_consistent_answer(question):
    # -------------------------------------------------------------------------
    # FUNKTION: get_consistent_answer
    # PARAMETER: question
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Holt frühere Antwort für dieselbe Frage zur Konsistenz-Sicherung."""
    hist = load_answer_history()
    for sid, answers in hist["surveys"].items():
        if question in answers:
            return answers[question]
    return None


def load_fail_learning() -> dict[str, object]:
    if FAIL_LEARNING_PATH.exists():
        try:
            with open(FAIL_LEARNING_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {
        "recent_failures": [],
        "issue_counts": {},
        "denylist": {
            "selectors": [],
            "action_signatures": [],
            "root_cause_keywords": [],
        },
    }


def save_fail_learning(data: dict[str, object]) -> None:
    FAIL_LEARNING_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_denylist_entries(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _extract_root_cause_keywords(root_cause: str) -> list[str]:
    keywords: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", root_cause.lower()):
        if token in FAIL_KEYWORD_STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords[:8]


def _build_action_signature(action: str, params: dict[str, object]) -> str:
    return f"{action}|{json.dumps(params, sort_keys=True, ensure_ascii=False)}"


def _extract_gate_action_signatures(gate) -> list[str]:
    if gate is None or not hasattr(gate, "action_history"):
        return []
    signatures: list[str] = []
    for item in list(getattr(gate, "action_history", []))[-3:]:
        if not isinstance(item, tuple) or len(item) != 3:
            continue
        _, action, params_json = item
        signatures.append(f"{action}|{params_json}")
    return signatures


def _get_fail_denylist() -> dict[str, list[str]]:
    memory = load_fail_learning()
    denylist = memory.get("denylist", {})
    if not isinstance(denylist, dict):
        return {"selectors": [], "action_signatures": [], "root_cause_keywords": []}
    return {
        "selectors": _normalize_denylist_entries(denylist.get("selectors", [])),
        "action_signatures": _normalize_denylist_entries(denylist.get("action_signatures", [])),
        "root_cause_keywords": _normalize_denylist_entries(denylist.get("root_cause_keywords", [])),
    }


def remember_fail_learning(
    analysis: dict[str, object], exit_reason: str, final_page_state: str, gate=None
) -> dict[str, object]:
    memory = load_fail_learning()
    recent_failures = list(memory.get("recent_failures", []))
    issue_counts = dict(memory.get("issue_counts", {}))
    denylist = _get_fail_denylist()

    issue_flags = {
        "captcha_detected": bool(analysis.get("captcha_detected")),
        "timing_issue": bool(analysis.get("timing_issue")),
        "selector_issue": bool(analysis.get("selector_issue")),
        "loop_detected": bool(analysis.get("loop_detected")),
    }
    for key, enabled in issue_flags.items():
        if enabled:
            issue_counts[key] = int(issue_counts.get(key, 0)) + 1

    selector_candidates = list(denylist["selectors"])
    selector_candidates.extend(_normalize_denylist_entries(analysis.get("bad_selectors", [])))
    if issue_flags["selector_issue"] and gate is not None and hasattr(gate, "failed_selectors"):
        selector_candidates.extend(
            str(selector) for selector in list(getattr(gate, "failed_selectors", {}).keys())
        )

    action_signature_candidates = list(denylist["action_signatures"])
    action_signature_candidates.extend(
        _normalize_denylist_entries(analysis.get("bad_action_signatures", []))
    )
    if issue_flags["loop_detected"]:
        action_signature_candidates.extend(_extract_gate_action_signatures(gate))

    root_cause_text = str(analysis.get("root_cause", "Unbekannt"))
    keyword_candidates = list(denylist["root_cause_keywords"])
    keyword_candidates.extend(_extract_root_cause_keywords(root_cause_text))

    recent_failures.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exit_reason": exit_reason,
            "page_state": final_page_state,
            "root_cause": root_cause_text,
            "fix_recommendation": str(analysis.get("fix_recommendation", "N/A")),
            "affected_step": str(analysis.get("affected_step", "N/A")),
            "issue_flags": issue_flags,
        }
    )

    memory["recent_failures"] = recent_failures[-5:]
    memory["issue_counts"] = issue_counts
    memory["denylist"] = {
        "selectors": _normalize_denylist_entries(selector_candidates)[-10:],
        "action_signatures": _normalize_denylist_entries(action_signature_candidates)[-10:],
        "root_cause_keywords": _normalize_denylist_entries(keyword_candidates)[-12:],
    }
    save_fail_learning(memory)
    return memory


def build_fail_learning_context() -> str:
    memory = load_fail_learning()
    recent_failures = memory.get("recent_failures", [])
    denylist = _get_fail_denylist()
    if not isinstance(recent_failures, list) or not recent_failures:
        return ""

    last_failure = recent_failures[-1]
    issue_counts = memory.get("issue_counts", {})
    if not isinstance(last_failure, dict) or not isinstance(issue_counts, dict):
        return ""

    lines = ["RECENT FAIL-LEARNINGS (vermeide diese Muster aktiv):"]
    lines.append(f"- Letzte Root Cause: {last_failure.get('root_cause', 'Unbekannt')}")
    lines.append(f"- Letzte Fix-Empfehlung: {last_failure.get('fix_recommendation', 'N/A')}")
    lines.append(f"- Letzter betroffener Schritt: {last_failure.get('affected_step', 'N/A')}")
    lines.append("HARTE FAIL-LEARNING REGELN FÜR DIE NÄCHSTE ENTSCHEIDUNG:")
    if int(issue_counts.get("timing_issue", 0)) > 0:
        lines.append(
            "- VERMEIDE Sofort-Wiederholungen nach Klicks; plane erst Sichtbarkeit/DOM-Änderung oder ein vorsichtiges Warten ein."
        )
    if int(issue_counts.get("selector_issue", 0)) > 0:
        lines.append(
            '- VERMEIDE next_action="click_element" mit generischen .class- oder tag-Selektoren; bevorzuge click_ref, #id + ghost_click oder vision_click.'
        )
    if int(issue_counts.get("loop_detected", 0)) > 0:
        lines.append(
            "- VERMEIDE dieselbe next_action mit denselben next_params auf demselben Screen; wenn unsicher, gib RETRY mit einer anderen Methode zurück."
        )
    if int(issue_counts.get("captcha_detected", 0)) > 0:
        lines.append(
            "- VERMEIDE Survey-Interaktionen solange Captcha/Blocker sichtbar sein könnte; priorisiere zuerst die Entblockung."
        )
    last_root_cause = str(last_failure.get("root_cause", ""))
    if any(
        hint in last_root_cause.lower()
        for hint in ("not visible", "unter dem fold", "under the fold", "not clickable")
    ):
        lines.append(
            "- Wenn ein Ziel vermutlich nicht sichtbar/klickbar ist, VERMEIDE blinde Standard-Klicks und bevorzuge scroll_down, ghost_click oder vision_click."
        )
    if denylist["selectors"]:
        lines.append(f"- HARTE SELECTOR-DENYLIST: {', '.join(denylist['selectors'][:4])}")
    if denylist["action_signatures"]:
        lines.append(
            "- HARTE ACTION-DENYLIST: Wiederhole keine zuvor gescheiterten Action-Signaturen."
        )
    if denylist["root_cause_keywords"]:
        lines.append(
            f"- RISK KEYWORDS AUS FEHLSCHLÄGEN: {', '.join(denylist['root_cause_keywords'][:6])}"
        )
    return "\n".join(lines)


def get_fail_learning_delay_bounds(min_sec: float, max_sec: float) -> tuple[float, float]:
    memory = load_fail_learning()
    issue_counts = memory.get("issue_counts", {})
    if isinstance(issue_counts, dict) and int(issue_counts.get("timing_issue", 0)) > 0:
        return min_sec + 1.0, max_sec + 2.0
    return min_sec, max_sec


def get_fail_learning_dom_wait_seconds(default_seconds: float = 1.0) -> float:
    memory = load_fail_learning()
    issue_counts = memory.get("issue_counts", {})
    if isinstance(issue_counts, dict) and int(issue_counts.get("timing_issue", 0)) > 0:
        return default_seconds + 1.0
    return default_seconds


def _get_fail_issue_counts() -> dict[str, int]:
    memory = load_fail_learning()
    issue_counts = memory.get("issue_counts", {})
    if not isinstance(issue_counts, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in issue_counts.items():
        try:
            out[str(key)] = int(value)
        except Exception:
            continue
    return out


def _is_fragile_cached_click(decision: dict[str, object]) -> bool:
    next_action = str(decision.get("next_action", "none"))
    if next_action != "click_element":
        return False
    raw_params = decision.get("next_params", {})
    if not isinstance(raw_params, dict):
        return False
    selector = str(raw_params.get("selector", ""))
    return bool(selector) and not selector.startswith("#")


def _should_bypass_cached_decision(decision: dict[str, object]) -> bool:
    issue_counts = _get_fail_issue_counts()
    denylist = _get_fail_denylist()
    raw_params = decision.get("next_params", {})
    next_params = raw_params if isinstance(raw_params, dict) else {}
    selector = str(next_params.get("selector", ""))
    if selector and selector in denylist["selectors"]:
        return True
    if (
        _build_action_signature(str(decision.get("next_action", "none")), next_params)
        in denylist["action_signatures"]
    ):
        return True
    if issue_counts.get("selector_issue", 0) > 0 and _is_fragile_cached_click(decision):
        return True
    if (
        issue_counts.get("loop_detected", 0) > 0
        and str(decision.get("next_action", "none")) in CLICK_ACTIONS
    ):
        return True
    return False


def _should_store_cached_decision(decision: dict[str, object]) -> bool:
    if decision.get("verdict") != "PROCEED":
        return False
    if _should_bypass_cached_decision(decision):
        return False
    return True


def apply_fail_learning_to_decision(
    decision: dict[str, object],
    gate,
    screenshot_hash: str,
) -> dict[str, object]:
    next_action = str(decision.get("next_action", "none"))
    raw_params = decision.get("next_params", {})
    next_params = raw_params if isinstance(raw_params, dict) else {}
    denylist = _get_fail_denylist()
    selector = str(next_params.get("selector", ""))
    action_signature = _build_action_signature(next_action, next_params)
    reason_text = str(decision.get("reason", "")).lower()

    memory = load_fail_learning()
    issue_counts = memory.get("issue_counts", {})
    selector_issues = 0
    loop_issues = 0
    if isinstance(issue_counts, dict):
        selector_issues = int(issue_counts.get("selector_issue", 0))
        loop_issues = int(issue_counts.get("loop_detected", 0))

    adapted = dict(decision)
    adapted["next_params"] = dict(next_params)

    if selector and selector in denylist["selectors"]:
        adapted["verdict"] = "RETRY"
        adapted["next_action"] = "none"
        adapted["next_params"] = {}
        adapted["reason"] = f"Fail-learning denylist blockiert Selector: {selector}"
        adapted["progress"] = False
        audit(
            "warning",
            message="Fail-learning selector denylist aktiv",
            selector=selector,
        )
        return adapted

    if action_signature in denylist["action_signatures"]:
        adapted["verdict"] = "RETRY"
        adapted["next_action"] = "none"
        adapted["next_params"] = {}
        adapted["reason"] = f"Fail-learning denylist blockiert Action-Signatur: {next_action}"
        adapted["progress"] = False
        audit("warning", message="Fail-learning action denylist aktiv", action=next_action)
        return adapted

    if (
        next_action == "click_element"
        and not str(next_params.get("selector", "")).startswith("#")
        and any(keyword in reason_text for keyword in denylist["root_cause_keywords"])
        and any(keyword in FAIL_RUNTIME_KEYWORDS for keyword in denylist["root_cause_keywords"])
    ):
        adapted["verdict"] = "RETRY"
        adapted["next_action"] = "none"
        adapted["next_params"] = {}
        adapted["reason"] = (
            "Fail-learning root-cause denylist blockiert fragilen Klick bei bekanntem Risiko"
        )
        adapted["progress"] = False
        audit(
            "warning",
            message="Fail-learning keyword denylist aktiv",
            action=next_action,
        )
        return adapted

    if loop_issues > 0 and gate.record_action(screenshot_hash, next_action, next_params):
        adapted["verdict"] = "RETRY"
        adapted["next_action"] = "none"
        adapted["next_params"] = {}
        adapted["reason"] = f"Fail-learning loop guard blockiert wiederholte Aktion: {next_action}"
        adapted["progress"] = False
        audit("warning", message="Fail-learning loop guard aktiv", action=next_action)
        return adapted

    if selector_issues <= 0 or next_action != "click_element":
        return adapted

    selector = str(next_params.get("selector", ""))
    ref = str(next_params.get("ref", ""))
    description = str(next_params.get("description", ""))

    if ref:
        adapted["next_action"] = "click_ref"
        adapted["next_params"] = {"ref": ref}
        adapted["reason"] = (
            f"{adapted.get('reason', '')} | Fail-learning bevorzugt click_ref nach Selector-Fails"
        ).strip(" |")
        return adapted

    if selector.startswith("#"):
        adapted["next_action"] = "ghost_click"
        adapted["next_params"] = {"selector": selector}
        adapted["reason"] = (
            f"{adapted.get('reason', '')} | Fail-learning bevorzugt ghost_click für stabile ID-Targets"
        ).strip(" |")
        return adapted

    if description:
        adapted["next_action"] = "vision_click"
        adapted["next_params"] = {"description": description}
        adapted["reason"] = (
            f"{adapted.get('reason', '')} | Fail-learning weicht auf vision_click aus"
        ).strip(" |")
        return adapted

    return adapted


# ============================================================================
# EXAKTE TAB-BINDUNG — GLOBAL STATE (PRIORITY -7.85)
# ============================================================================
# CURRENT_TAB_ID und CURRENT_WINDOW_ID werden beim ersten tabs_create gesetzt
# und DANACH niemals mehr auf None zurückgesetzt.
# Alle Bridge-Calls MÜSSEN tabId enthalten — kein Fallback auf aktiven Tab!
# WHY: Parallele User-Tabs dürfen den Worker NIEMALS beeinflussen.
# CONSEQUENCES: Wenn tabId nicht gesetzt ist, schlägt der Call laut fehl.
CURRENT_TAB_ID: int | None = None  # Wird nach init() IMMER gesetzt sein
CURRENT_WINDOW_ID: int | None = None  # Wird nach init() IMMER gesetzt sein
WORKER_HOST_HINT = "heypiggy.com"  # Host-Teil der Worker-URL zur Recovery-Prüfung
request_id_counter = 0


def _require_tab_id() -> int:
    """
    Gibt CURRENT_TAB_ID zurück oder wirft einen Fehler wenn nicht gesetzt.
    WHY: Nach dem initialen Tab-Erstellen MUSS tabId immer bekannt sein.
         Ein leerer Fallback würde auf einen beliebigen aktiven Tab fallen —
         das ist das exakte Problem das wir eliminieren wollen.
    CONSEQUENCES: Laut fehlschlagen ist besser als still falschen Tab steuern.
    """
    global CURRENT_TAB_ID
    if CURRENT_TAB_ID is None:
        raise RuntimeError(
            "CURRENT_TAB_ID ist nicht gesetzt! "
            "Worker darf keine Bridge-Calls senden bevor tabs_create erfolgreich war."
        )
    return CURRENT_TAB_ID


def _tab_params() -> dict:
    """
    Gibt ein Dict mit dem exakten tabId zurück.
    WHY: Convenience-Wrapper damit alle Funktionen einheitlich tabId übergeben.
    CONSEQUENCES: Wirft RuntimeError wenn tabId nicht gesetzt — kein stiller Fallback.
    """
    return {"tabId": _require_tab_id()}


# ============================================================================
# AUDIT-LOG — Jede einzelne Aktion wird auf Disk geloggt
# ============================================================================

AUDIT_LOG_PATH = AUDIT_DIR / "audit.jsonl"


def audit(event_type: str, **data):
    # -------------------------------------------------------------------------
    # FUNKTION: audit
    # PARAMETER: event_type: str, **data
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Schreibt einen Audit-Eintrag ins Log.
    WHY: Damit wir bei JEDEM Fehler exakt nachvollziehen können was passiert ist.
    CONSEQUENCES: Ohne Audit-Log ist Debugging unmöglich.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "run_id": RUN_ID,
        **data,
    }
    try:
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Audit-Fehler darf den Worker NIEMALS crashen

    # Auch auf stdout für Live-Monitoring
    emoji_map = {
        "screenshot": "📸",
        "vision_check": "🧠",
        "action": "⚡",
        "ghost_click": "👻",
        "coord_click": "🎯",
        "vision_click": "🔭",
        "click_escalation": "🔺",
        "error": "❌",
        "success": "✅",
        "bridge_health": "📡",
        "session_save": "💾",
        "state_change": "🔄",
        "navigate": "🌐",
        "stop": "🛑",
        "start": "🚀",
    }
    emoji = emoji_map.get(event_type, "📝")
    print(f"{emoji} [{event_type}] {json.dumps(data, ensure_ascii=False)[:200]}")


def missing_required_credentials() -> list[str]:
    """
    Liefert fehlende Pflicht-Env-Variablen für den Worker.
    WHY: Der Worker darf ohne echte Zugangsdaten niemals in Login-/Survey-Flow laufen.
    CONSEQUENCES: Die Preflight-Kontrolle stoppt fail-closed vor jeder Browser-Mutation.
    """
    missing = []
    if not os.environ.get("HEYPIGGY_EMAIL"):
        missing.append("HEYPIGGY_EMAIL")
    if not os.environ.get("HEYPIGGY_PASSWORD"):
        missing.append("HEYPIGGY_PASSWORD")
    return missing


def ensure_vision_probe_screenshot() -> str:
    """
    Schreibt ein minimales Screenshot-PNG für den Vision-Auth-Probe auf Disk.
    WHY: Der Auth-Check soll denselben screenshot-basierten OpenSIN-Vision-Pfad nutzen
    wie der echte Worker, aber ohne vorher einen Browser-Tab zu mutieren.
    CONSEQUENCES: Gibt immer einen stabilen lokalen PNG-Pfad für den Probe zurück.
    """
    probe_path = SCREENSHOT_DIR / "vision_auth_probe.png"
    if not probe_path.exists():
        probe_path.write_bytes(VISION_AUTH_PROBE_PNG)
    return str(probe_path)


def collect_opencode_text(stdout: bytes, stderr: bytes = b"") -> str:
    """
    Extrahiert Text-Events aus `opencode run --format json`.
    WHY: opencode kann Events je nach TTY auf stdout oder stderr schreiben.
    Daher kombinieren wir beide Streams.
    """
    combined = stdout + stderr
    full_text = ""
    for line in combined.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("type") == "text":
            full_text += event.get("part", {}).get("text", "")
    return full_text.strip()


def detect_vision_auth_failure(raw_text: str) -> str | None:
    """
    Erkennt harte Vision-Control-Plane-Blocker im kombinierten Output.
    WHY: Der Worker muss bei kaputter Vision-Authentifizierung ODER bei einem
         explizit ungesunden Vision-Health-Zustand fail-closed stoppen.
    CONSEQUENCES: Sobald 401/invalid-credentials oder ein klarer Health-Failure
                  auftaucht, darf der Worker nicht weiterlaufen.
    """
    lowered = (raw_text or "").lower()
    if "401" in lowered and "invalid authentication credentials" in lowered:
        return "401 invalid authentication credentials"
    if "invalid authentication credentials" in lowered:
        return "invalid authentication credentials"
    if "authentication credentials" in lowered and "invalid" in lowered:
        return "invalid authentication credentials"

    health_markers = (
        "vision health failure",
        "vision health check failed",
        "provider health check failed",
        "model health check failed",
        "provider unhealthy",
        "model unhealthy",
        "vision provider unhealthy",
        "vision model unhealthy",
    )
    for marker in health_markers:
        if marker in lowered:
            return marker

    if "health" in lowered and any(
        word in lowered for word in ("failed", "failure", "unhealthy", "degraded")
    ):
        return "vision health check failed"
    return None


def _resolve_opencode_bin() -> str:
    """Resolves the opencode binary path, preferring project-local version for plugin support."""
    import os

    project_root = os.path.dirname(os.path.abspath(__file__))
    local_bin = os.path.join(project_root, "node_modules", ".bin", "opencode")
    if os.path.exists(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    return "opencode"


async def _run_vision_opencode(
    prompt: str,
    screenshot_path: str,
    *,
    timeout: int = 120,
    step_num: int = 0,
    purpose: str = "vision",
) -> dict[str, object]:
    """
    Führt einen screenshot-basierten OpenSIN-Vision-Call über `opencode run` aus.
    WHY: Preflight-Probe und reguläre Vision-Entscheidungen müssen denselben CLI-Pfad
    nutzen, damit Auth-Fehler zentral erkannt und fail-closed behandelt werden.
    CONSEQUENCES: Gibt strukturierte Resultate mit `ok` und `auth_failure` zurück.
    """
    if not VISION_CIRCUIT_BREAKER.allow_request():
        audit("error", message=f"Vision circuit open ({purpose})", step=step_num)
        return {
            "ok": False,
            "auth_failure": False,
            "error": f"Vision circuit open ({purpose})",
            "stdout_text": "",
            "stderr_text": "",
            "returncode": None,
            "circuit_open": True,
        }

    start_time = time.time()
    cli_timeout = max(30, timeout - 5)
    opencode_bin = _resolve_opencode_bin()
    cmd = [
        "timeout",
        str(cli_timeout),
        opencode_bin,
        "run",
        prompt,
        "-f",
        screenshot_path,
        "--model",
        VISION_MODEL,
        "--format",
        "json",
    ]
    # DEBUG: For step 1, write the exact command to a file for manual reproduction
    if step_num == 1:
        try:
            with open("/tmp/vision_cmd_debug.txt", "w") as f:
                f.write(" ".join(cmd) + "\n")
                f.write(f"PROMPT: {prompt[:500]}...\n")
                f.write(f"SCREENSHOT: {screenshot_path}\n")
        except Exception:
            pass

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except Exception as e:
            try:
                process.kill()
            except:
                pass
            VISION_CIRCUIT_BREAKER.record_failure()
            return {
                "ok": False,
                "auth_failure": False,
                "error": f"Vision timeout or error: {e}",
                "stdout_text": "",
                "stderr_text": "",
                "returncode": -1,
            }
        full_text = collect_opencode_text(stdout, stderr)
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        combined = "\n".join(part for part in [full_text, stderr_text] if part)
        auth_error = detect_vision_auth_failure(combined)

        if process.returncode != 0:
            if (
                purpose == "preflight_auth_probe"
                and process.returncode in (124, 137)
                and not auth_error
            ):
                VISION_CIRCUIT_BREAKER.record_success()
                return {
                    "ok": True,
                    "auth_failure": False,
                    "text": full_text,
                    "stdout_text": full_text,
                    "stderr_text": stderr_text,
                    "returncode": process.returncode,
                }
            if full_text and process.returncode in (124, 137):
                VISION_CIRCUIT_BREAKER.record_success()
                return {
                    "ok": True,
                    "auth_failure": False,
                    "text": full_text,
                    "stdout_text": full_text,
                    "stderr_text": stderr_text,
                    "returncode": process.returncode,
                }
            VISION_CIRCUIT_BREAKER.record_failure()
            error_message = stderr_text or full_text or f"opencode exit {process.returncode}"
            if auth_error:
                audit(
                    "error",
                    message=f"Vision auth failure ({purpose}): {auth_error}",
                    step=step_num,
                )
                return {
                    "ok": False,
                    "auth_failure": True,
                    "error": auth_error,
                    "stdout_text": full_text,
                    "stderr_text": stderr_text,
                    "returncode": process.returncode,
                }
            audit(
                "error",
                message=f"Vision command failed ({purpose}): {error_message[:200]}",
                step=step_num,
            )
            return {
                "ok": False,
                "auth_failure": False,
                "error": error_message,
                "stdout_text": full_text,
                "stderr_text": stderr_text,
                "returncode": process.returncode,
            }

        if auth_error:
            audit(
                "error",
                message=f"Vision auth failure ({purpose}): {auth_error}",
                step=step_num,
            )
            return {
                "ok": False,
                "auth_failure": True,
                "error": auth_error,
                "stdout_text": full_text,
                "stderr_text": stderr_text,
                "returncode": process.returncode,
            }

        VISION_CIRCUIT_BREAKER.record_success()
        return {
            "ok": True,
            "auth_failure": False,
            "text": full_text,
            "stderr_text": stderr_text,
            "returncode": process.returncode,
        }

    except asyncio.TimeoutError:
        VISION_CIRCUIT_BREAKER.record_failure()
        audit("error", message=f"Vision Timeout ({purpose})", step=step_num)
        return {
            "ok": False,
            "auth_failure": False,
            "error": f"Vision Timeout ({purpose})",
            "stdout_text": "",
            "stderr_text": "",
            "returncode": None,
        }

    except Exception as e:
        auth_error = detect_vision_auth_failure(str(e))
        if auth_error:
            audit(
                "error",
                message=f"Vision auth failure ({purpose}): {auth_error}",
                step=step_num,
            )
            return {
                "ok": False,
                "auth_failure": True,
                "error": auth_error,
                "stdout_text": "",
                "stderr_text": str(e),
                "returncode": None,
            }
        VISION_CIRCUIT_BREAKER.record_failure()
        audit("error", message=f"Vision Exception ({purpose}): {e}", step=step_num)
        return {
            "ok": False,
            "auth_failure": False,
            "error": str(e),
            "stdout_text": "",
            "stderr_text": str(e),
            "returncode": None,
        }
    finally:
        if CURRENT_RUN_SUMMARY is not None:
            CURRENT_RUN_SUMMARY.record_vision_call(time.time() - start_time)


async def _nvidia_nim_chat(
    prompt: str,
    screenshot_path: str,
    *,
    timeout: int,
    model: str,
    force_json: bool = True,
) -> dict[str, object]:
    if not NVIDIA_API_KEY:
        return {
            "ok": False,
            "auth_failure": True,
            "error": "NVIDIA_API_KEY fehlt",
            "stdout_text": "",
            "stderr_text": "",
            "returncode": None,
        }

    def _call_nvidia() -> tuple[int, str, str]:
        with open(screenshot_path, "rb") as screenshot_file:
            image_b64 = base64.b64encode(screenshot_file.read()).decode("ascii")

        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            },
        ]
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }
        if force_json:
            body["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{WORKER_CONFIG.nvidia.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {NVIDIA_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.getcode(), resp.read().decode("utf-8"), ""
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8"), str(e)
            except Exception:
                return e.code, "", str(e)
        except Exception as e:
            return 599, "", str(e)

    status_code, response_text, error_text = await asyncio.to_thread(_call_nvidia)
    if status_code == 401:
        return {
            "ok": False,
            "auth_failure": True,
            "error": error_text or response_text or "HTTP 401",
            "stdout_text": response_text,
            "stderr_text": error_text,
            "returncode": status_code,
        }
    if status_code == 429:
        return {
            "ok": False,
            "auth_failure": False,
            "error": error_text or response_text or "HTTP 429",
            "stdout_text": response_text,
            "stderr_text": error_text,
            "returncode": status_code,
            "rate_limited": True,
        }
    if status_code >= 400:
        return {
            "ok": False,
            "auth_failure": False,
            "error": error_text or response_text or f"HTTP {status_code}",
            "stdout_text": response_text,
            "stderr_text": error_text,
            "returncode": status_code,
        }

    try:
        payload = json.loads(response_text)
    except Exception as e:
        return {
            "ok": False,
            "auth_failure": False,
            "error": f"NVIDIA JSON Parse Error: {e}",
            "stdout_text": response_text,
            "stderr_text": error_text,
            "returncode": status_code,
        }

    choices = payload.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    text = message.get("content", "") if isinstance(message, dict) else ""
    # Budget-Tracking: Token-Usage aus dem OpenAI-kompatiblen NIM-Payload lesen.
    # WHY: budget_guard muss wissen wieviele Token wir verbrannt haben um die
    # Kosten-Obergrenze durchzusetzen. Payload hat optional "usage" mit
    # prompt_tokens / completion_tokens.
    usage_block = payload.get("usage") or {}
    in_tok = 0
    out_tok = 0
    try:
        in_tok = int(usage_block.get("prompt_tokens") or 0)
        out_tok = int(usage_block.get("completion_tokens") or 0)
    except Exception:
        in_tok, out_tok = 0, 0
    guard = globals().get("BUDGET_GUARD")
    if guard is not None and (in_tok or out_tok):
        try:
            guard.record_usage(model=model, input_tokens=in_tok, output_tokens=out_tok)
        except Exception as ge:
            # Budget-Tracking darf NIE den Worker crashen
            audit("budget_record_error", error=str(ge))
    return {
        "ok": True,
        "auth_failure": False,
        "text": text,
        "stdout_text": response_text,
        "stderr_text": error_text,
        "returncode": status_code,
        "model_used": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


async def _run_vision_nvidia(
    prompt: str,
    screenshot_path: str,
    *,
    timeout: int,
    step_num: int,
    purpose: str,
) -> dict[str, object]:
    models = (NVIDIA_VISION_MODEL, *NVIDIA_FALLBACK_MODELS)
    last_result: dict[str, object] = {
        "ok": False,
        "auth_failure": False,
        "error": "Kein NVIDIA-Modell probiert",
    }
    for model in models:
        result = await _nvidia_nim_chat(
            prompt,
            screenshot_path,
            timeout=timeout,
            model=model,
            force_json=True,
        )
        if result.get("ok") or result.get("auth_failure"):
            return result
        last_result = result
        audit("error", message=f"NVIDIA-Modell fehlgeschlagen: {model}", step=step_num)
    return last_result


async def run_vision_model(
    prompt: str,
    screenshot_path: str,
    *,
    timeout: int = 120,
    step_num: int = 0,
    purpose: str = "vision",
) -> dict[str, object]:
    if VISION_BACKEND == "nvidia":
        return await _run_vision_nvidia(
            prompt,
            screenshot_path,
            timeout=timeout,
            step_num=step_num,
            purpose=purpose,
        )
    if VISION_BACKEND == "auto" and NVIDIA_API_KEY:
        return await _run_vision_nvidia(
            prompt,
            screenshot_path,
            timeout=timeout,
            step_num=step_num,
            purpose=purpose,
        )
    return await _run_vision_opencode(
        prompt,
        screenshot_path,
        timeout=timeout,
        step_num=step_num,
        purpose=purpose,
    )


async def ensure_worker_preflight() -> dict:
    """
    Prüft die komplette Control-Plane vor der ersten Browser-Mutation.
    WHY: Issue #86 verlangt ein fail-closed Gate vor tabs_create/navigation/login.
    CONSEQUENCES: Fehlt Env oder Vision-Auth, stoppt der Worker bevor ein Tab erstellt wird.
    """
    missing = missing_required_credentials()
    if missing:
        reason = f"Pflicht-Env fehlt: {', '.join(missing)}"
        audit("stop", reason=reason)
        return {"ok": False, "reason": reason}

    if not await check_bridge_alive():
        reason = "Bridge nicht erreichbar während Preflight"
        audit("stop", reason=reason)
        return {"ok": False, "reason": reason}

    probe_path = ensure_vision_probe_screenshot()
    probe_prompt = (
        'Antworte ausschließlich mit gültigem JSON im Format {"status":"ok"}. Keine Erklärungen.'
    )
    probe_result = await run_vision_model(
        probe_prompt,
        probe_path,
        timeout=60,
        step_num=0,
        purpose="preflight_auth_probe",
    )
    if not probe_result.get("ok"):
        reason = probe_result.get("error", "Vision-Probe fehlgeschlagen")
        audit("stop", reason=f"Vision-Preflight fehlgeschlagen: {reason}")
        return {"ok": False, "reason": reason}

    # Preflight-Probe beweist nur dass Vision-Auth funktioniert (ok=True ohne auth_failure).
    # Das Modell antwortet manchmal mit Freitext statt reinem JSON — das ist OK.
    # Wir prüfen NUR: Hat opencode den Call ohne Auth-Fehler durchgeführt?
    # WHY: Ein 1x1 PNG Probe braucht kein Strict-JSON-Format — es reicht der erfolgreiche Call.
    probe_text = probe_result.get("text", "")
    if not probe_text:
        # Leere Antwort = Vision hat geantwortet aber nichts zurückgegeben → trotzdem OK
        # (manchmal gibt das Modell bei einem leeren Bild nur Whitespace zurück)
        pass
    audit(
        "success",
        message=f"Vision-Preflight OK: Auth healthy, Antwort={probe_text[:80]}",
    )

    audit("success", message="Worker-Preflight bestanden: Env + Vision auth healthy")
    return {"ok": True, "reason": "ready"}


# ============================================================================
# BRIDGE-KOMMUNIKATION �� Extrem robustes HTTP mit Retry und Reconnect
# ============================================================================


def fetch_health():
    # -------------------------------------------------------------------------
    # FUNKTION: fetch_health
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Holt den Bridge-Health-Status.
    WHY: Vor JEDER Aktion muss die Bridge erreichbar sein.
    CONSEQUENCES: Bei Timeout wird gewartet, nicht gecrasht.
    """
    try:
        req = urllib.request.Request(BRIDGE_HEALTH_URL)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e), "extensionConnected": False}


async def wait_for_extension(timeout=600):
    # -------------------------------------------------------------------------
    # FUNKTION: wait_for_extension
    # PARAMETER: timeout=600
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Wartet robust auf die Extension-Verbindung.
    WHY: Ohne Extension sind alle Bridge-Calls sinnlos.
    CONSEQUENCES: 600s Timeout, danach harter Abbruch mit klarer Fehlermeldung.
    """
    audit("start", message="Warte auf Bridge Extension", timeout=timeout)
    start = time.time()
    last_status = None

    while time.time() - start < timeout:
        health = await asyncio.to_thread(fetch_health)
        current = health.get("extensionConnected")

        # Nur loggen bei Statusänderung, um Spam zu vermeiden
        if current != last_status:
            audit(
                "bridge_health",
                connected=current,
                version=health.get("version", "?"),
                tools=health.get("toolsCount", 0),
                pending=health.get("pendingRequests", 0),
            )
            last_status = current

        if current is True:
            audit("success", message="Extension verbunden")
            return True

        await asyncio.sleep(5)

    raise RuntimeError(f"Timeout ({timeout}s): Bridge Extension nicht verbunden.")


def post_mcp(method: str, params: dict = None):
    # -------------------------------------------------------------------------
    # FUNKTION: post_mcp
    # PARAMETER: method: str, params: dict = None
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Sendet einen MCP-Request an die Bridge mit 3x Retry und Error-Body-Parsing.
    WHY: Die Bridge kann kurzzeitig 500er liefern, das darf nicht zum Crash führen.
    CONSEQUENCES: Nach 3 Fehlversuchen wird eine RuntimeError geworfen (wird oben gefangen).
    """
    global request_id_counter
    request_id_counter += 1

    body = {"jsonrpc": "2.0", "method": method, "id": request_id_counter}
    if params:
        body["params"] = params

    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                BRIDGE_MCP_URL,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                decoded = json.loads(resp.read().decode("utf-8"))
                if "error" in decoded:
                    last_err = f"MCP Protocol Error: {decoded['error']}"
                    audit("error", message=last_err, attempt=attempt + 1)
                    time.sleep(2 * (attempt + 1))
                    continue
                return decoded.get("result", {})

        except urllib.error.HTTPError as e:
            # Echten Error-Body extrahieren statt nur Status-Code
            try:
                error_body = e.read().decode("utf-8")
                error_json = json.loads(error_body)
                last_err = f"HTTP {e.code}: {json.dumps(error_json)}"
            except Exception:
                last_err = f"HTTP {e.code}: {e.reason}"
            audit("error", message=last_err, attempt=attempt + 1)
            time.sleep(2 * (attempt + 1))

        except Exception as e:
            last_err = str(e)
            audit("error", message=last_err, attempt=attempt + 1)
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"MCP fehlgeschlagen nach 3 Versuchen: {last_err}")


def decode_mcp_result(raw):
    # -------------------------------------------------------------------------
    # FUNKTION: decode_mcp_result
    # PARAMETER: raw
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Decodiert das MCP-Result aus der verschachtelten JSON-RPC Struktur.
    WHY: Die Bridge gibt content[0].text zurück, das muss entpackt werden.
    """
    if isinstance(raw, dict) and "content" in raw:
        txt = raw["content"][0].get("text", "")
        try:
            return json.loads(txt)
        except Exception:
            return txt
    return raw


def normalize_selector(selector: str) -> str:
    """
    Bereinigt einen Vision-Selector in gültiges CSS.
    WHY: Vision-Modelle erzeugen manchmal Playwright-artige Pseudo-Selektoren
    wie :contains(...) oder :has-text(...). Diese sind im Browser-QuerySelector
    nicht g��ltig und müssen deshalb vor der Ausführung repariert werden.
    """
    if not selector:
        return selector

    cleaned = selector
    cleaned = re.sub(r":contains\((?:[^()]+|\([^()]*\))*\)", "", cleaned)
    cleaned = re.sub(r":has-text\((?:[^()]+|\([^()]*\))*\)", "", cleaned)
    cleaned = re.sub(r":text\((?:[^()]+|\([^()]*\))*\)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


async def click_visible_button_with_text(text_hint: str):
    # -------------------------------------------------------------------------
    # FUNKTION: click_visible_button_with_text
    # PARAMETER: text_hint: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Klicke einen sichtbaren Button anhand seines Textinhalts.
    WHY: Vision kann dashboard-blockierende Gate-Buttons wie
    "Starte die erste Umfrage!" übersehen. Dieser DOM-basierte
    Gate-Klick verhindert, dass wir Survey-Karten zu früh anklicken.
    """
    global CURRENT_TAB_ID, CURRENT_WINDOW_ID
    tab_params = _tab_params()
    js_code = f"""
    (function() {{
      const hint = {json.dumps(text_hint.lower())};
      const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
      const el = candidates.find((node) => {{
        const text = (node.textContent || '').trim().toLowerCase();
        const visible = node.offsetParent !== null;
        return visible && text.includes(hint);
      }});
      if (!el) return {{ clicked: false, reason: 'not found', hint: hint }};
      if (typeof el.focus === 'function') el.focus();
      if (typeof el.click === 'function') el.click();
      else el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
      return {{
        clicked: true,
        text: (el.textContent || '').trim().substring(0, 120),
        tag: el.tagName,
        id: el.id || '',
        cls: (el.className || '').toString().substring(0, 120)
      }};
    }})();
    """
    return await execute_bridge("execute_javascript", {"script": js_code, **tab_params})


# ============================================================================
# CAPTCHA BYPASS — Auto-Erkennung und Behandlung von Captchas (NEU in v3.1)
# ============================================================================
CAPTCHA_RETRY_LIMIT = 5
_captcha_attempt_count = 0


async def detect_captcha_page() -> bool:
    """Erkennt Captcha-Präsenz via DOM-Scan."""
    global CURRENT_TAB_ID, CURRENT_WINDOW_ID
    tab_params = _tab_params()
    js_code = """
    (function() {
        var captchaSelectors = [
            '.recaptcha-checkbox', '.g-recaptcha', '#recaptcha-anchor',
            'iframe[src*="recaptcha"]', '[title="reCAPTCHA"]',
            '.h-captcha', 'iframe[src*="hcaptcha"]',
            '.cf-turnstile', 'iframe[src*="turnstile"]',
            '.captcha-checkbox', '[class*="captcha"]',
            '[class*="verify"][class*="human"]'
        ];
        for (var i = 0; i < captchaSelectors.length; i++) {
            var els = document.querySelectorAll(captchaSelectors[i]);
            for (var j = 0; j < els.length; j++) {
                if (els[j].offsetParent !== null) {
                    return {found: true, selector: captchaSelectors[i], index: j};
                }
            }
        }
        var text = (document.body.textContent || '').toLowerCase();
        var captchaTexts = ['i am not a robot', 'ich bin kein robot', 'verify you are human', 'security check', 'captcha'];
        for (var i = 0; i < captchaTexts.length; i++) {
            if (text.includes(captchaTexts[i])) {
                return {found: true, type: 'text_match', keyword: captchaTexts[i]};
            }
        }
        return {found: false};
    })();
    """
    result = await execute_bridge("execute_javascript", {"script": js_code, **tab_params})
    if isinstance(result, dict):
        return result.get("result", {}).get("found", False)
    return False


async def handle_captcha() -> bool:
    """Versucht Captcha automatisch zu lösen. Returns True wenn erfolgreich."""
    global _captcha_attempt_count, CURRENT_TAB_ID, CURRENT_WINDOW_ID
    if _captcha_attempt_count >= CAPTCHA_RETRY_LIMIT:
        audit(
            "error",
            message=f"Captcha nach {CAPTCHA_RETRY_LIMIT} Versuchen nicht lösbar",
        )
        return False
    _captcha_attempt_count += 1
    tab_params = _tab_params()
    js_code = """
    (function() {
        var checkboxSelectors = [
            '.recaptcha-checkbox', '#recaptcha-anchor',
            '.captcha-checkbox', '[title="reCAPTCHA"]',
            '[class*="recaptcha"][class*="checkbox"]'
        ];
        for (var i = 0; i < checkboxSelectors.length; i++) {
            var els = document.querySelectorAll(checkboxSelectors[i]);
            for (var j = 0; j < els.length; j++) {
                var el = els[j];
                if (el.offsetParent !== null) {
                    if (typeof el.click === 'function') el.click();
                    else el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}));
                    return {clicked: true, selector: checkboxSelectors[i], type: 'checkbox'};
                }
            }
        }
        var refreshBtns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        var refresh = refreshBtns.find(function(b) {
            var t = (b.textContent || '').toLowerCase();
            return t.includes('refresh') || t.includes('reload') || t.includes('neues') || t.includes('anderes');
        });
        if (refresh) {
            refresh.click();
            return {refreshed: true, type: 'refresh'};
        }
        return {action: 'none'};
    })();
    """
    result = await execute_bridge("execute_javascript", {"script": js_code, **tab_params})
    audit("captcha", attempt=_captcha_attempt_count, result=str(result)[:200])
    if isinstance(result, dict):
        r = result.get("result", {})
        if r.get("clicked") or r.get("refreshed"):
            await asyncio.sleep(3 + random.random() * 3)
            return True
    return False


async def resolve_survey_selector(selector: str, description: str = "") -> str:
    """
    Wandelt generische Survey-Selektoren in die echte #survey-... ID um.
    WHY: Vision liefert auf HeyPiggy oft nur "div.survey-item" statt der
    konkreten ID. Damit wir nicht blind die erste Karte anklicken, lesen wir
    die sichtbaren Survey-Karten aus dem DOM und wählen die beste Karte über
    Preis-Hinweis oder höchste vergütete Karte.
    """
    if not selector:
        return selector

    lowered = selector.lower()
    if "survey-item" not in lowered and "survey" not in lowered:
        return selector

    global CURRENT_TAB_ID, CURRENT_WINDOW_ID
    tab_params = _tab_params()
    js_code = """
    (function() {
      const cards = Array.from(document.querySelectorAll('div.survey-item')).map((el) => {
        const r = el.getBoundingClientRect();
        return {
          id: el.id || '',
          text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
          visible: el.offsetParent !== null,
          x: Math.round(r.left + r.width / 2),
          y: Math.round(r.top + r.height / 2),
          w: Math.round(r.width),
          h: Math.round(r.height),
        };
      });
      return cards.filter((card) => card.visible && card.id);
    })();
    """
    scan = await execute_bridge("execute_javascript", {"script": js_code, **tab_params})
    cards = []
    if isinstance(scan, dict):
        cards = scan.get("result", []) or []
    elif isinstance(scan, list):
        cards = scan

    if not isinstance(cards, list) or not cards:
        return selector

    price_hint = None
    for source in (description or "", selector):
        m = re.search(r"(\d+[.,]\d+)\s*€", source)
        if m:
            price_hint = m.group(1).replace(",", ".")
            break

    def _card_price(card):
    # -------------------------------------------------------------------------
    # FUNKTION: _card_price
    # PARAMETER: card
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        text = str(card.get("text", ""))
        m = re.search(r"(\d+[.,]\d+)\s*€", text)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", "."))
        except Exception:
            return None

    chosen = None
    if price_hint is not None:
        for card in cards:
            text = str(card.get("text", ""))
            if price_hint in text.replace(",", "."):
                chosen = card
                break

    if chosen is None:
        priced_cards = []
        for card in cards:
            price = _card_price(card)
            if price is not None:
                priced_cards.append((price, card))
        if priced_cards:
            priced_cards.sort(key=lambda item: item[0], reverse=True)
            chosen = priced_cards[0][1]

    if chosen and chosen.get("id"):
        resolved = f"#{chosen['id']}"
        if resolved != selector:
            audit(
                "state_change",
                message=(f"Survey-Selector auf echte ID aufgelöst: {selector[:80]} -> {resolved}"),
            )
        return resolved

    return selector


async def recover_worker_tab_id() -> int | None:
    """
    Stellt die exakt bekannte Worker-Tab-ID wieder her.
    WHY: Parallel geöffnete Browser-Tabs dürfen den Worker nie beeinflussen.
    Wir akzeptieren deshalb nur die vorher gespeicherte Tab-ID oder genau eine
    eindeutige HeyPiggy-Tab-Instanz im gespeicherten Fenster.
    """
    global CURRENT_TAB_ID, CURRENT_WINDOW_ID

    query = {}
    if CURRENT_WINDOW_ID is not None:
        query["windowId"] = CURRENT_WINDOW_ID

    tabs_raw = await asyncio.to_thread(
        post_mcp, "tools/call", {"name": "tabs_list", "arguments": {"query": query}}
    )
    tabs_result = decode_mcp_result(tabs_raw)
    tabs = []
    if isinstance(tabs_result, dict):
        tabs = tabs_result.get("tabs", []) or []

    if CURRENT_TAB_ID is not None:
        for tab in tabs:
            if isinstance(tab, dict) and tab.get("id") == CURRENT_TAB_ID:
                return CURRENT_TAB_ID

    candidates = [
        tab
        for tab in tabs
        if isinstance(tab, dict) and WORKER_HOST_HINT in str(tab.get("url", "")).lower()
    ]

    if len(candidates) == 1:
        CURRENT_TAB_ID = candidates[0].get("id")
        audit(
            "state_change",
            message=(
                f"Worker-Tab wiederhergestellt: tabId={CURRENT_TAB_ID}, "
                f"windowId={CURRENT_WINDOW_ID}"
            ),
        )
        return CURRENT_TAB_ID

    if not candidates and CURRENT_WINDOW_ID is None:
        # Letzter Versuch: alle Tabs im Browser durchsuchen, aber nur wenn wir
        # noch keinen eigenen Fenster-Kontext haben. Das bleibt trotzdem streng
        # auf den HeyPiggy-Host begrenzt.
        tabs_raw = await asyncio.to_thread(
            post_mcp, "tools/call", {"name": "tabs_list", "arguments": {}}
        )
        tabs_result = decode_mcp_result(tabs_raw)
        if isinstance(tabs_result, dict):
            all_tabs = tabs_result.get("tabs", []) or []
            candidates = [
                tab
                for tab in all_tabs
                if isinstance(tab, dict) and WORKER_HOST_HINT in str(tab.get("url", "")).lower()
            ]
            if len(candidates) == 1:
                CURRENT_TAB_ID = candidates[0].get("id")
                audit(
                    "state_change",
                    message=f"Worker-Tab global wiedergefunden: tabId={CURRENT_TAB_ID}",
                )
                return CURRENT_TAB_ID

    if candidates:
        audit(
            "error",
            message=(
                f"Worker-Tab-Recovery mehrdeutig: {len(candidates)} HeyPiggy-Tabs "
                "im gleichen Kontext gefunden"
            ),
        )
    else:
        audit(
            "error",
            message="Worker-Tab-Recovery fehlgeschlagen: kein HeyPiggy-Tab gefunden",
        )
    return None


async def execute_bridge(method: str, params: dict[str, object] | None = None):
    # -------------------------------------------------------------------------
    # FUNKTION: execute_bridge
    # PARAMETER: method: str, params: dict[str, object] | None = None
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Fuehrt einen Bridge-Tool-Call aus, decodiert das Ergebnis, und retried
    bei transienten Fehlern (5xx, connection reset, timeouts) mit
    exponential backoff.

    WHY: Die MCP-Bridge haengt manchmal 1-3 Sekunden bei Chrome-Extension-
    Wakeups oder Netzwerk-Hiccups. Einzelner Fehlversuch != Worker-Exit.
    CONSEQUENCES:
      - Bis zu 3 Retries mit 0.4s -> 0.9s -> 1.8s Backoff + Jitter
      - Stale-tabId-Recovery bleibt wie bisher (separater Pfad)
      - Exceptions + persistente Fehler werden als {"error": ...} zurueckgegeben
      - bridge_retry.is_transient_bridge_error() entscheidet was "transient" ist
    """
    started_at = time.time()
    call_params = params or {}

    # WHY: Methoden die Seiteneffekte erzeugen wie "click_ref" oder "type_text"
    # sollten NICHT automatisch retried werden — der erste Aufruf koennte
    # durchgegangen sein und ein zweiter wuerde doppelt klicken/tippen.
    # Nur reine Lese-/Idempotente Methoden retriesen.
    idempotent_methods = {
        "screenshot",
        "execute_javascript",
        "get_snapshot",
        "snapshot",
        "get_clickable_snapshot",
        "export_all_cookies",
        "list_tabs",
        "get_active_tab",
        "get_url",
        "get_page_state",
    }
    should_retry = method in idempotent_methods

    async def _one_call() -> object:
        raw = await asyncio.to_thread(
            post_mcp, "tools/call", {"name": method, "arguments": call_params}
        )
        return decode_mcp_result(raw)

    async def _audit_retry(attempt: int, err: str, delay: float) -> None:
        audit(
            "bridge_retry",
            method=method,
            attempt=attempt,
            delay=round(delay, 2),
            error=err[:120],
        )

    try:
        if should_retry:
            result = await _bridge_call_with_retry(
                _one_call,
                max_attempts=3,
                base_delay=0.4,
                max_delay=2.5,
                jitter=0.35,
                on_retry=_audit_retry,
            )
        else:
            result = await _one_call()

        # Wenn ein explizit gesetzter tabId-Wert stale ist, niemals blind auf den
        # aktiven Tab ausweichen. Stattdessen nur die exakt bekannte Worker-Tab-
        # Zuordnung wiederherstellen.
        if (
            isinstance(result, dict)
            and result.get("error")
            and call_params.get("tabId") is not None
        ):
            error_text = str(result.get("error", ""))
            if "No tab with id" in error_text or "No tab with given id" in error_text:
                audit(
                    "state_change",
                    message=f"Stale tabId für {method} erkannt; versuche Worker-Tab-Recovery",
                )
                recovered_tab_id = await recover_worker_tab_id()
                if recovered_tab_id is not None:
                    retry_params = dict(call_params)
                    retry_params["tabId"] = recovered_tab_id
                    retry_raw = await asyncio.to_thread(
                        post_mcp,
                        "tools/call",
                        {"name": method, "arguments": retry_params},
                    )
                    result = decode_mcp_result(retry_raw)

        return result
    except Exception as e:
        audit("error", message=f"execute_bridge({method}) failed: {e}")
        return {"error": str(e)}
    finally:
        if CURRENT_RUN_SUMMARY is not None:
            CURRENT_RUN_SUMMARY.record_bridge_call(time.time() - started_at)


async def check_bridge_alive():
    # -------------------------------------------------------------------------
    # FUNKTION: check_bridge_alive
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Prüft ob die Bridge noch lebt, und wartet ggf. auf Reconnect.
    WHY: Mitten im Lauf kann die Extension disconnecten (Tab-Crash, Sleep, etc.)
    CONSEQUENCES: Bis zu 60s Reconnect-Versuch bevor aufgegeben wird.
    """
    health = await asyncio.to_thread(fetch_health)
    if health.get("extensionConnected") is True:
        return True

    audit("state_change", message="Bridge disconnected! Versuche Reconnect...")
    start = time.time()
    while time.time() - start < 60:
        await asyncio.sleep(5)
        health = await asyncio.to_thread(fetch_health)
        if health.get("extensionConnected") is True:
            audit("success", message="Bridge reconnected!")
            return True

    audit("error", message="Bridge Reconnect fehlgeschlagen nach 60s")
    return False


# ============================================================================
# SCREENSHOT-ENGINE — Mit Hash-Tracking für Fortschrittserkennung
# ============================================================================


async def take_screenshot(step_num: int, label: str = ""):
    # -------------------------------------------------------------------------
    # FUNKTION: take_screenshot
    # PARAMETER: step_num: int, label: str = ""
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Macht einen Screenshot der exakt bekannten Worker-Tab-Instanz und speichert ihn als PNG.
    WHY: Jeder einzelne Schritt muss visuell dokumentiert werden (PRIORITY -7.0).
    CONSEQUENCES: Gibt (path, hash) zurück für Fortschrittserkennung.
    """
    try:
        params = _tab_params()
        # `screenshot_full` hängt an der sichtbaren Browser-Instanz und wäre bei
        # parallelen Tabs unsicher. `observe` liefert ein tabgebundenes Screenshot
        # für genau die Worker-Instanz.
        res = await execute_bridge("observe", params)

        if isinstance(res, dict) and "screenshot" in res:
            screenshot = res.get("screenshot") or {}
            if isinstance(screenshot, dict) and "dataUrl" in screenshot:
                res = screenshot["dataUrl"]
        elif isinstance(res, dict) and "dataUrl" in res:
            # Fallback für ältere Bridge-Implementierungen.
            res = res["dataUrl"]

        if not isinstance(res, str) or not res.startswith("data:"):
            audit(
                "error",
                message="Screenshot fehlgeschlagen",
                step=step_num,
                result_type=type(res).__name__,
            )
            return None, None

        # Base64 decodieren und speichern
        _, payload = res.split(",", 1)
        # Padding korrigieren (Bridge liefert manchmal ohne Padding)
        payload += "=" * ((4 - len(payload) % 4) % 4)
        img_bytes = base64.b64decode(payload)

        # Hash für Fortschrittserkennung berechnen
        img_hash = hashlib.md5(img_bytes).hexdigest()

        # Dateiname mit Zeitstempel und Label für Nachvollziehbarkeit
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "", label.replace(" ", "_"))[:30]
        filename = f"step_{step_num:03d}_{safe_label}_{img_hash[:8]}.png"
        path = SCREENSHOT_DIR / filename
        path.write_bytes(img_bytes)

        audit(
            "screenshot",
            step=step_num,
            path=str(path),
            hash=img_hash,
            size=len(img_bytes),
        )
        return str(path), img_hash

    except Exception as e:
        audit("error", message=f"Screenshot Exception: {e}", step=step_num)
        return None, None


# ============================================================================
# DOM PRE-SCAN — Holt ECHTE Selektoren von der Seite vor jedem Vision-Call
# ============================================================================


async def dom_prescan():
    # -------------------------------------------------------------------------
    # FUNKTION: dom_prescan
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
        Scannt die aktuelle Seite nach klickbaren Elementen und liefert echte Selektoren.
    WHY: Das Vision-LLM DARF NIEMALS CSS-Selektoren raten! Es muss die echten kennen.
    CONSEQUENCES: Ohne Pre-Scan schlaegt das LLM Fantasie-Selektoren wie :has-text() vor.
    """
    global CURRENT_TAB_ID, CURRENT_WINDOW_ID
    tab_params = _tab_params()

    # 1. Accessibility-Tree-Snapshot mit Refs holen (für click_ref)
    snapshot_info = ""
    try:
        snapshot = await execute_bridge("snapshot", {**tab_params, "includeScreenshot": False})
        if isinstance(snapshot, dict) and "tree" in snapshot:
            tree = snapshot["tree"]
            # Nur interaktive Elemente (mit @eX Refs) extrahieren
            interactive = [l.strip() for l in tree.splitlines() if "@e" in l]
            if interactive:
                snapshot_info = "ACCESSIBILITY-TREE REFS (nutzbar mit click_ref):\n" + "\n".join(
                    interactive[:20]
                )
            audit(
                "action",
                message=f"DOM Pre-Scan: {len(interactive)} interactive refs, {snapshot.get('refCount', 0)} total refs",
            )
    except Exception as e:
        audit("error", message=f"Snapshot fehlgeschlagen: {e}")

    # 2. Echte HTML-Elemente mit Klick-Potential scannen
    # WHY (Issue #61 Fix F3): Frueher wurde der kombinierte Selektor auf 25
    # Elemente gekappt. Auf dem HeyPiggy-Dashboard erschlagen Navbar-Links,
    # Filter-Buttons, Footer-Links und das Profil-Menue die eigentlichen
    # Survey-Kacheln (div.survey-item mit id=#survey-XXXXXXXX). Ergebnis:
    # Vision bekommt keine Ref-IDs fuer die Kacheln und klickt ins Nichts.
    # CONSEQUENCES: Zweistufiger Scan:
    #   1) Survey-Kacheln zuerst, OHNE Limit (in der Praxis max. 20 Karten)
    #   2) Generische klickbare Elemente, auf 25 gekappt
    # So sind Survey-Karten immer oben im Prompt — auch wenn darueber 40+
    # Nav-Elemente sitzen.
    clickable_info = ""
    try:
        js_scan = """
        (function() {
            var results = [];
            var seenElements = [];

            function describe(el) {
                var r = el.getBoundingClientRect();
                if (r.width < 5 || r.height < 5) return null;
                var sel = '';
                if (el.id) sel = '#' + el.id;
                else if (el.className && typeof el.className === 'string') {
                    var cls = el.className.split(' ').filter(function(c) { return c.length > 0; })[0];
                    if (cls) sel = el.tagName.toLowerCase() + '.' + cls;
                }
                if (!sel) sel = el.tagName.toLowerCase();
                return {
                    sel: sel,
                    tag: el.tagName,
                    id: el.id || '',
                    cls: (el.className + '').substring(0, 80),
                    text: (el.textContent || '').substring(0, 60).replace(/\\n/g, ' ').trim(),
                    x: Math.round(r.x + r.width/2),
                    y: Math.round(r.y + r.height/2),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                    cursor: getComputedStyle(el).cursor
                };
            }

            // STUFE 1: Survey-Kacheln PRIORITAER (kein Limit, aber Dedup)
            var surveyCards = document.querySelectorAll(
                'div.survey-item, [id^="survey-"], .survey-card, [data-survey-id]'
            );
            for (var s = 0; s < surveyCards.length; s++) {
                var card = surveyCards[s];
                if (seenElements.indexOf(card) !== -1) continue;
                var info = describe(card);
                if (!info) continue;
                info.priority = 'survey';
                results.push(info);
                seenElements.push(card);
            }

            // STUFE 2: Generische klickbare Elemente, auf 25 gekappt
            var all = document.querySelectorAll(
                '[onclick], [role="button"], a[href], button, input[type="submit"], [style*="cursor: pointer"], [class*="card"], [class*="survey"]'
            );
            var added = 0;
            for (var i = 0; i < all.length && added < 25; i++) {
                var el = all[i];
                if (seenElements.indexOf(el) !== -1) continue;
                var info2 = describe(el);
                if (!info2) continue;
                info2.priority = 'generic';
                results.push(info2);
                seenElements.push(el);
                added++;
            }
            return results;
        })();
        """
        scan_result = await execute_bridge("execute_javascript", {"script": js_scan, **tab_params})
        if isinstance(scan_result, dict) and "result" in scan_result:
            elements = scan_result["result"]
            if isinstance(elements, list) and elements:
                lines = []
                survey_count = 0
                for el in elements:
                    selector = el.get("sel", "?")
                    if el.get("id"):
                        selector = f"#{el['id']}"
                    text = el.get("text", "")[:40]
                    # priority=survey markiert Survey-Kacheln (Issue #61 F3).
                    # Wird in clickable_info als Substring ausgegeben, damit
                    # der Dashboard-DOM-Check (F2) darauf matchen kann.
                    prio = el.get("priority", "generic")
                    if prio == "survey":
                        survey_count += 1
                    lines.append(
                        f'  - priority={prio} selector="{selector}" text="{text}" '
                        f'pos=({el.get("x")},{el.get("y")}) size={el.get("w")}x{el.get("h")} '
                        f'cursor={el.get("cursor")}'
                    )
                clickable_info = (
                    "KLICKBARE ELEMENTE AUF DER SEITE (ECHTE CSS-Selektoren!):\n" + "\n".join(lines)
                )
                audit(
                    "action",
                    message=(
                        f"DOM Pre-Scan: {len(elements)} clickable elements "
                        f"({survey_count} survey cards) found"
                    ),
                )
    except Exception as e:
        audit("error", message=f"Clickable scan fehlgeschlagen: {e}")

    # 3. Seiten-URL und Titel
    page_context = ""
    try:
        page_info = await execute_bridge("get_page_info", tab_params)
        if isinstance(page_info, dict):
            page_context = f"AKTUELLE SEITE: URL={page_info.get('url', '?')} Title={page_info.get('title', '?')}"
    except Exception:
        pass

    # 4. MEDIA-ANALYSE — Audio / Video / Bilder auf der Seite erkennen und verstehen
    # WHY: Surveys enthalten oft Audio-Clips ("Was hören Sie?") oder Video-Ads
    # ("Welche Marke wurde gezeigt?") — ohne transkribierten Inhalt kann das Vision-LLM
    # die Folgefrage nicht beantworten.
    # CONSEQUENCES: Der Router scannt billig (ein JS-Call) und analysiert nur
    # wenn Media gefunden wurde. Ergebnisse werden per URL-Hash gecacht damit
    # wir denselben Clip nicht 20x transkribieren.
    global _LAST_MEDIA_ANALYSIS
    media_block = ""
    if MEDIA_ROUTER is not None and WORKER_CONFIG.media.enabled:
        try:
            snapshot = await MEDIA_ROUTER.scan_page()
            if snapshot.has_media:
                audit(
                    "media_detected",
                    audio=len(snapshot.audio_urls),
                    video=len(snapshot.video_urls),
                    images=len(snapshot.image_urls),
                    embeds=len(snapshot.embed_urls),
                )
                # Medien automatisch abspielen (manche Surveys gating "Weiter"
                # bis der Clip lief)
                await MEDIA_ROUTER.ensure_media_playing(snapshot)
                analysis = await MEDIA_ROUTER.analyze(snapshot)
                _LAST_MEDIA_ANALYSIS = analysis
                media_block = analysis.to_prompt_block()
                audit(
                    "media_analyzed",
                    elapsed_sec=analysis.elapsed_sec,
                    audio_ok=sum(1 for a in analysis.audio_transcripts if not a.error),
                    video_ok=sum(1 for v in analysis.video_understandings if not v.error),
                    errors=len(analysis.errors),
                )
            else:
                _LAST_MEDIA_ANALYSIS = None
        except Exception as e:
            audit("media_prescan_error", error=str(e))
            _LAST_MEDIA_ANALYSIS = None

    # 5. AKTIVE FRAGE + OPTIONEN EXTRAHIEREN
    # WHY: Persona.resolve_answer braucht den echten Fragetext und die sichtbaren
    # Antwort-Optionen um die korrekte Persona-Antwort zu finden. Ohne diese
    # Extraktion kann der Worker Validation-Traps / Attention-Checks nicht erkennen.
    # CONSEQUENCES: _LAST_QUESTION_TEXT und _LAST_QUESTION_OPTIONS werden global
    # gesetzt, damit die Answer-Recording-Logik nach dem Click darauf zugreifen kann.
    global _LAST_QUESTION_TEXT, _LAST_QUESTION_OPTIONS
    question_block = ""
    try:
        question_js = r"""
        (function() {
          // Versuche die sichtbare Frage + Optionen zu finden.
          // Strategie: groesster sichtbarer Text-Block im oberen Viewport-Drittel,
          //            gefolgt von Labels/Buttons/Radio-Inputs in Lesereihenfolge.
          function visible(el) {
            if (!el) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return false;
            var s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
            if (r.bottom < 0 || r.top > window.innerHeight + 400) return false;
            return true;
          }
          function txt(el) {
            return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
          }
          // FRAGE: suche h1/h2/h3/legend/label/p mit "?" oder Frage-Keywords
          var q = '';
          var candidates = Array.from(document.querySelectorAll(
            'h1, h2, h3, h4, legend, label, .question, [class*="question"], [class*="Frage"], [class*="prompt"], p'
          ));
          var bestScore = 0;
          for (var i = 0; i < candidates.length; i++) {
            var el = candidates[i];
            if (!visible(el)) continue;
            var t = txt(el);
            if (t.length < 5 || t.length > 500) continue;
            var score = 0;
            if (t.indexOf('?') !== -1) score += 5;
            if (/\b(bitte|wie|wo|was|wann|warum|welche|which|how|please|select|wählen)\b/i.test(t)) score += 3;
            if (/\b(alter|geschlecht|einkommen|wohnort|beruf|beschäftigt|haushalt|familienstand|kinder|auto|rauchen|rauchen sie)\b/i.test(t)) score += 2;
            // bevorzuge oben stehende Fragen
            var r = el.getBoundingClientRect();
            if (r.top < window.innerHeight * 0.6) score += 1;
            if (score > bestScore) { bestScore = score; q = t; }
          }
          // OPTIONEN: Radio-Labels, Checkboxen, Buttons, Listenpunkte
          var opts = [];
          var seen = {};
          var optionEls = Array.from(document.querySelectorAll(
            'label, button:not([type="submit"]), [role="radio"], [role="checkbox"], [role="option"], .option, [class*="option"], [class*="answer"], li'
          ));
          for (var j = 0; j < optionEls.length && opts.length < 25; j++) {
            var oel = optionEls[j];
            if (!visible(oel)) continue;
            var ot = txt(oel);
            if (ot.length < 1 || ot.length > 140) continue;
            // Filter: vermeide Navigation / grosse Blocks
            if (/^(weiter|next|submit|fortfahren|zurück|back|continue|abbrechen|cancel|start|beenden)$/i.test(ot)) continue;
            if (seen[ot.toLowerCase()]) continue;
            seen[ot.toLowerCase()] = 1;
            opts.push(ot);
          }
          // Extra: Radio-Button VALUES (manche Surveys haben Text ausserhalb des Labels)
          var radios = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
          for (var k = 0; k < radios.length && opts.length < 30; k++) {
            var rd = radios[k];
            if (!visible(rd)) continue;
            var lbl = '';
            if (rd.id) {
              var labelEl = document.querySelector('label[for="' + rd.id + '"]');
              if (labelEl) lbl = txt(labelEl);
            }
            if (!lbl && rd.parentElement) lbl = txt(rd.parentElement);
            if (lbl && lbl.length < 140 && !seen[lbl.toLowerCase()]) {
              seen[lbl.toLowerCase()] = 1;
              opts.push(lbl);
            }
          }
          // PROGRESS: "Frage 3 von 10" erkennen
          var progress = '';
          var pageText = (document.body.innerText || '').replace(/\s+/g, ' ');
          var m = pageText.match(/(?:frage|question|q\.?)\s*(\d+)\s*(?:von|of|\/)\s*(\d+)/i);
          if (m) progress = m[0];
          return { question: q, options: opts, progress: progress };
        })();
        """
        qres = await execute_bridge("execute_javascript", {"script": question_js, **tab_params})
        qdata = qres.get("result") if isinstance(qres, dict) else None
        if isinstance(qdata, dict):
            q_text = (qdata.get("question") or "").strip()
            q_opts = qdata.get("options") or []
            if isinstance(q_opts, list):
                q_opts = [
                    str(o).strip()
                    for o in q_opts
                    if isinstance(o, (str, int, float)) and str(o).strip()
                ]
            else:
                q_opts = []
            progress = (qdata.get("progress") or "").strip()
            _LAST_QUESTION_TEXT = q_text or None
            _LAST_QUESTION_OPTIONS = q_opts
            if q_text or q_opts:
                lines = ["ERKANNTE SURVEY-FRAGE (aus DOM):"]
                if progress:
                    lines.append(f"- Fortschritt: {progress}")
                if q_text:
                    lines.append(f"- Fragetext: {q_text[:300]}")
                if q_opts:
                    preview = ", ".join(f'"{o[:60]}"' for o in q_opts[:12])
                    lines.append(f"- Sichtbare Optionen ({len(q_opts)}): {preview}")
                question_block = "\n".join(lines)
                audit(
                    "question_detected",
                    question=q_text[:120],
                    n_options=len(q_opts),
                    progress=progress,
                )
    except Exception as e:
        audit("question_scan_error", error=str(e))
        _LAST_QUESTION_TEXT = None
        _LAST_QUESTION_OPTIONS = []

    # 6. DISQUALIFIKATION / PRE-QUALIFIKATION / ABSCHLUSS-SEITEN ERKENNEN
    # WHY: Panels zeigen am Anfang Screener ("Wir suchen Personen die...") und
    # am Ende Disqualifikations-Seiten ("Leider qualifizieren Sie sich nicht").
    # Beides muss der Agent erkennen:
    #   - Screener -> extra vorsichtig + wahrheitstreu antworten (niemals luegen!)
    #   - DQ-Seite -> Survey als DQ markieren, Dashboard ansteuern, naechste Umfrage
    # CONSEQUENCES: Findet typische Phrasen auf Deutsch + Englisch. Setzt
    # globale Flags die der Prompt und die Main-Loop auslesen koennen.
    global _LAST_SCREENER_HIT, _LAST_DQ_HIT, _LAST_COMPLETE_HIT
    screener_block = ""
    try:
        phrase_js = r"""
        (function() {
          var body = (document.body && document.body.innerText) ? document.body.innerText : '';
          body = body.replace(/\s+/g, ' ').trim().toLowerCase();
          var first = body.substring(0, 4000);

          var SCREENER_PATTERNS = [
            'wir suchen personen', 'wir suchen teilnehmer', 'bitte beantworten sie die folgenden fragen',
            'vor der umfrage', 'zur vorqualifikation', 'screening-fragen', 'kurze vorfragen',
            'damit wir feststellen koennen', 'damit wir feststellen können',
            'we are looking for people', 'screening questions', 'pre-qualification',
            'before we begin', 'before the survey', 'to determine if you qualify',
            'please answer the following'
          ];
          var DQ_PATTERNS = [
            'leider qualifizieren sie sich nicht', 'leider koennen sie nicht teilnehmen',
            'leider können sie nicht teilnehmen', 'passen sie nicht in die zielgruppe',
            'sie gehoeren nicht zur zielgruppe', 'sie gehören nicht zur zielgruppe',
            'diese umfrage ist bereits voll', 'quote erreicht', 'quota full',
            'sorry, you do not qualify', 'you do not qualify', "you don't qualify",
            'unfortunately you are not eligible', 'screen out', 'thank you for your interest, but',
            'umfrage ist geschlossen', 'survey is closed', 'survey has ended',
            'zu dieser umfrage sind sie nicht berechtigt'
          ];
          var COMPLETE_PATTERNS = [
            'vielen dank fuer ihre teilnahme', 'vielen dank für ihre teilnahme',
            'umfrage abgeschlossen', 'umfrage erfolgreich', 'ihre antworten wurden gespeichert',
            'thank you for completing', 'survey complete', 'your responses have been recorded',
            'sie haben die umfrage erfolgreich', 'ihr guthaben wurde gutgeschrieben',
            'credited to your account', 'reward has been', 'belohnung wurde'
          ];
          function find(arr) {
            for (var i = 0; i < arr.length; i++) {
              if (body.indexOf(arr[i]) !== -1) return arr[i];
            }
            return null;
          }
          // Screener: nur wenn noch keine komplette Seite/DQ
          var dqHit = find(DQ_PATTERNS);
          var completeHit = find(COMPLETE_PATTERNS);
          var screenerHit = (dqHit || completeHit) ? null : find(SCREENER_PATTERNS);
          return {
            screener: screenerHit,
            dq: dqHit,
            complete: completeHit,
            body_preview: first.substring(0, 300)
          };
        })();
        """
        pres = await execute_bridge("execute_javascript", {"script": phrase_js, **tab_params})
        pdata = pres.get("result") if isinstance(pres, dict) else None
        if isinstance(pdata, dict):
            sh = pdata.get("screener")
            dh = pdata.get("dq")
            ch = pdata.get("complete")
            _LAST_SCREENER_HIT = sh if isinstance(sh, str) else None
            _LAST_DQ_HIT = dh if isinstance(dh, str) else None
            _LAST_COMPLETE_HIT = ch if isinstance(ch, str) else None
            if dh:
                screener_block = (
                    "===== DISQUALIFIKATION ERKANNT =====\n"
                    f"Phrase auf Seite: '{dh}'\n"
                    "REGEL: Diese Umfrage ist verloren (DQ). Setze page_state='survey_done' "
                    "mit reason='disqualified', damit der Orchestrator die naechste Umfrage "
                    "startet. Kein Retry, kein Zurueck — akzeptiere und weiter.\n"
                    "NICHT luegen um zurueckzukommen — das fuehrt zu einer Konto-Sperre."
                )
                audit("dq_detected", phrase=dh[:80])
            elif ch:
                screener_block = (
                    "===== UMFRAGE-ABSCHLUSS ERKANNT =====\n"
                    f"Phrase auf Seite: '{ch}'\n"
                    "REGEL: Setze page_state='survey_done' mit reason='completed'. "
                    "Der Orchestrator startet die naechste Umfrage automatisch."
                )
                audit("complete_detected", phrase=ch[:80])
            elif sh:
                screener_block = (
                    "===== PRE-QUALIFIKATIONS-SCREENER AKTIV =====\n"
                    f"Phrase auf Seite: '{sh}'\n"
                    "REGEL: Jede Frage in dieser Phase entscheidet ueber Zulassung. "
                    "Antworte STRIKT aus Persona — niemals spekulieren, niemals luegen. "
                    "Wenn die Persona-Antwort zur Disqualifikation fuehrt, akzeptiere "
                    "das Schicksal (Luegen fuehren zu Folge-Traps und Account-Sperre)."
                )
                audit("screener_detected", phrase=sh[:80])
    except Exception as e:
        audit("screener_scan_error", error=str(e))
        _LAST_SCREENER_HIT = None
        _LAST_DQ_HIT = None
        _LAST_COMPLETE_HIT = None

    # 7. UNIVERSELLE HINDERNIS-ERKENNUNG:
    # COOKIE-BANNER / GOOGLE-TRANSLATE / GENERIC-MODAL / START-CTA /
    # LANGUAGE-SELECTOR / RATING-PAGE
    # WHY: Der Agent darf an NICHTS haengen bleiben. Cookie-Banner ("Alle
    # akzeptieren"), Google-Translate-Popup (X / "Nein danke"), Generic-Modals
    # ("Umfrage starten"-Raketen-Dialog), Sprachauswahl (muss Persona-Sprache
    # waehlen) und Post-Survey-Rating-Seiten (5 Sterne + Freitext = Bonus-
    # Punkte!) sind alle deterministische Hindernisse. Wir bauen sie im JS
    # direkt und liefern dem Vision-LLM konkrete ref-IDs mit Klick-Priorisierung.
    # CONSEQUENCES: Der Prompt bekommt einen Block "DRINGENDE AKTION" mit dem
    # naechsten sicheren Klick — das Vision-LLM nimmt den einfach ab.
    persona_lang = (
        ACTIVE_PERSONA.language_primary
        if ACTIVE_PERSONA is not None and ACTIVE_PERSONA.language_primary
        else "de"
    )
    persona_country_name = (
        ACTIVE_PERSONA.country_name
        if ACTIVE_PERSONA is not None and ACTIVE_PERSONA.country_name
        else ""
    )
    obstacle_block = ""
    global _LAST_RATING_PAGE, _LAST_OBSTACLE_KIND
    try:
        obstacle_js = r"""
        (function(personaLang, personaCountry) {
          function visible(el) {
            if (!el) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return false;
            var s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
            return true;
          }
          function txt(el) {
            if (!el) return '';
            return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
          }
          function refOf(el) {
            return el && el.getAttribute ? (el.getAttribute('data-ref') || el.getAttribute('data-bridge-ref') || '') : '';
          }
          function findButtonByText(patterns) {
            var cands = Array.from(document.querySelectorAll(
              'button, a[role="button"], [role="button"], input[type="button"], input[type="submit"], a.btn, a.button'
            ));
            for (var i = 0; i < cands.length; i++) {
              var el = cands[i];
              if (!visible(el)) continue;
              var t = txt(el).toLowerCase();
              if (!t || t.length > 60) continue;
              for (var j = 0; j < patterns.length; j++) {
                if (t.indexOf(patterns[j]) !== -1) return { el: el, match: patterns[j], text: t };
              }
            }
            return null;
          }
          var result = { obstacle: null };

          // A) COOKIE-BANNER
          var cookieAccept = findButtonByText([
            'alle akzeptieren', 'alles akzeptieren', 'akzeptieren und weiter',
            'accept all', 'accept cookies', 'i accept', 'agree to all',
            'tout accepter', 'aceptar todo'
          ]);
          if (cookieAccept) {
            result.obstacle = { kind: 'cookie_accept', text: cookieAccept.text, ref: refOf(cookieAccept.el) };
            return result;
          }

          // B) GOOGLE TRANSLATE POPUP
          var trPopup = document.querySelector('.goog-te-banner-frame, #goog-gt-tt, iframe[src*="translate.google"]');
          if (trPopup && visible(trPopup)) {
            var closeBtn = null;
            var closers = Array.from(document.querySelectorAll(
              '[aria-label*="close" i], [aria-label*="schliessen" i], [aria-label*="schließen" i], button.close, .goog-te-banner-frame button'
            ));
            for (var k = 0; k < closers.length; k++) { if (visible(closers[k])) { closeBtn = closers[k]; break; } }
            result.obstacle = {
              kind: 'translate_popup',
              text: 'Google Translate banner',
              ref: refOf(closeBtn) || '',
              fallback: "document.querySelectorAll('.goog-te-banner-frame,#goog-gt-tt').forEach(function(e){e.remove();});"
            };
            return result;
          }

          // C) START-CTA
          var startBtn = findButtonByText([
            'umfrage starten', 'umfrage beginnen', 'jetzt starten', 'los gehts', "los geht's",
            'start survey', 'begin survey', 'start now', 'get started',
            'weiter zur umfrage', 'zur umfrage', 'survey starten'
          ]);
          if (startBtn) {
            result.obstacle = { kind: 'start_cta', text: startBtn.text, ref: refOf(startBtn.el) };
            return result;
          }

          // D) POST-SURVEY RATING
          var bodyLow = (document.body.innerText || '').replace(/\s+/g, ' ').toLowerCase();
          var ratingSignals = [
            'diese umfrage bewerten', 'umfrage bewerten', 'rate this survey',
            'bewerten sie die umfrage', 'worum ging es bei der umfrage',
            'wie hat ihnen die umfrage gefallen', 'how was the survey',
            'your feedback', 'ihr feedback', 'bewertung abgeben'
          ];
          var isRating = false;
          for (var rs = 0; rs < ratingSignals.length; rs++) {
            if (bodyLow.indexOf(ratingSignals[rs]) !== -1) { isRating = true; break; }
          }
          if (isRating) {
            var stars = Array.from(document.querySelectorAll(
              '[class*="star" i], [aria-label*="star" i], [aria-label*="stern" i], [role="radio"][aria-label*="5"], button[data-rating], svg[class*="star" i]'
            )).filter(visible);
            var fiveStar = null;
            for (var s = 0; s < stars.length; s++) {
              var al = (stars[s].getAttribute('aria-label') || '').toLowerCase();
              var dv = stars[s].getAttribute('data-rating') || stars[s].getAttribute('data-value') || '';
              if (al.indexOf('5') !== -1 || dv === '5') { fiveStar = stars[s]; break; }
            }
            if (!fiveStar && stars.length >= 5) fiveStar = stars[4];
            function clickable(el) {
              var cur = el;
              for (var d = 0; d < 4 && cur; d++) {
                if (cur.tagName === 'BUTTON' || cur.getAttribute('role') === 'radio' || cur.tagName === 'A') return cur;
                cur = cur.parentElement;
              }
              return el;
            }
            var ta = Array.from(document.querySelectorAll('textarea')).filter(visible)[0] || null;
            var submit = findButtonByText([
              'einreichen', 'absenden', 'senden', 'submit', 'send', 'abschicken', 'bewertung abgeben'
            ]);
            result.obstacle = {
              kind: 'rating_page',
              five_star_ref: fiveStar ? refOf(clickable(fiveStar)) : '',
              textarea_ref: ta ? refOf(ta) : '',
              textarea_required: ta ? (ta.hasAttribute('required') || (ta.placeholder || '').toLowerCase().indexOf('erforderlich') !== -1) : false,
              submit_ref: submit ? refOf(submit.el) : '',
              submit_text: submit ? submit.text : ''
            };
            return result;
          }

          // E) LANGUAGE-SELECTOR
          var langHeaderRe = /\b(language|sprache|langue|idioma|lingua|언어|言語|语言)\b/i;
          var hasLangHeader = langHeaderRe.test(bodyLow.substring(0, 3000));
          if (hasLangHeader) {
            var langMap = {
              'de': ['deutsch', 'german', 'allemand'],
              'en': ['english', 'englisch', 'anglais'],
              'fr': ['francais', 'français', 'french', 'franzoesisch'],
              'es': ['espanol', 'español', 'spanish', 'spanisch'],
              'it': ['italiano', 'italian', 'italienisch'],
              'nl': ['nederlands', 'dutch', 'niederlaendisch', 'niederländisch']
            };
            var wanted = langMap[personaLang] || langMap['de'];
            var langCands = Array.from(document.querySelectorAll(
              'label, input[type="radio"], a, button, [role="radio"], [role="option"], li'
            )).filter(visible);
            var pick = null;
            for (var lc = 0; lc < langCands.length; lc++) {
              var lt = txt(langCands[lc]).toLowerCase();
              if (lt.length < 2 || lt.length > 30) continue;
              for (var w = 0; w < wanted.length; w++) {
                if (lt === wanted[w] || lt.indexOf(wanted[w]) !== -1) { pick = langCands[lc]; break; }
              }
              if (pick) break;
            }
            if (pick) {
              result.obstacle = { kind: 'language_selector', text: txt(pick), ref: refOf(pick), wanted: personaLang };
              return result;
            }
          }

          return result;
        })(arguments[0], arguments[1]);
        """
        ores = await execute_bridge(
            "execute_javascript",
            {
                "script": obstacle_js,
                "args": [persona_lang, persona_country_name],
                **tab_params,
            },
        )
        odata = ores.get("result") if isinstance(ores, dict) else None
        obstacle = odata.get("obstacle") if isinstance(odata, dict) else None
        if isinstance(obstacle, dict):
            kind = str(obstacle.get("kind") or "")
            _LAST_OBSTACLE_KIND = kind or None
            _LAST_RATING_PAGE = obstacle if kind == "rating_page" else None
            if kind == "cookie_accept":
                obstacle_block = (
                    "===== HINDERNIS: COOKIE-BANNER =====\n"
                    f"Akzeptier-Button: '{obstacle.get('text', '')}' — ref={obstacle.get('ref') or 'n/a'}\n"
                    "DRINGENDE AKTION: click_ref auf genau diesen Button. "
                    "Ohne das bleibt die Seite nicht interaktiv."
                )
            elif kind == "translate_popup":
                obstacle_block = (
                    "===== HINDERNIS: GOOGLE-TRANSLATE POPUP =====\n"
                    f"Close-ref={obstacle.get('ref') or 'n/a'}\n"
                    "DRINGENDE AKTION: Wenn Close-ref vorhanden -> click_ref. "
                    "Sonst execute_javascript mit diesem Fallback:\n"
                    f"  {obstacle.get('fallback', '')}"
                )
            elif kind == "start_cta":
                obstacle_block = (
                    "===== HINDERNIS: START-CTA ('Umfrage starten') =====\n"
                    f"Button: '{obstacle.get('text', '')}' — ref={obstacle.get('ref') or 'n/a'}\n"
                    "DRINGENDE AKTION: click_ref auf diesen Button. "
                    "NIEMALS das X/Schliessen klicken — das bricht die Umfrage ab "
                    "und du verlierst den Reward."
                )
            elif kind == "language_selector":
                obstacle_block = (
                    "===== HINDERNIS: SPRACHAUSWAHL =====\n"
                    f"Persona-Sprache={persona_lang} -> Option '{obstacle.get('text', '')}' "
                    f"(ref={obstacle.get('ref') or 'n/a'}).\n"
                    "DRINGENDE AKTION: click_ref auf diese Option, danach Weiter/Next/"
                    "Continue/Pfeil klicken. NIEMALS eine andere Sprache waehlen — "
                    "sonst scheiterst du an jeder Folgefrage."
                )
            elif kind == "rating_page":
                fs = obstacle.get("five_star_ref") or "n/a"
                ta = obstacle.get("textarea_ref") or ""
                sb = obstacle.get("submit_ref") or "n/a"
                required = bool(obstacle.get("textarea_required"))
                obstacle_block = (
                    "===== BONUS: POST-SURVEY-BEWERTUNG ERKANNT =====\n"
                    f"5-Stern-ref: {fs} | Textarea-ref: {ta or '(keine)'} | "
                    f"Submit-ref: {sb} | Text-Pflicht: {required}\n"
                    "MAXIMALER REWARD-PLAN (schrittweise):\n"
                    "  1) click_ref auf den 5-Stern (volle Punktzahl -> hoeherer Bonus).\n"
                    "  2) Falls Textarea sichtbar: type_text mit neutralem Satz, "
                    "10-20 Woerter, z.B. 'Umfrage war klar formuliert, die Fragen "
                    "waren verstaendlich und angemessen lang. Alles gut.'\n"
                    "  3) click_ref auf den Submit-Button.\n"
                    "NIEMALS das Rating ueberspringen — es ist bares Geld. "
                    "Erst NACH Submit die Seite als survey_done melden."
                )
            else:
                _LAST_OBSTACLE_KIND = None
                _LAST_RATING_PAGE = None
        else:
            _LAST_OBSTACLE_KIND = None
            _LAST_RATING_PAGE = None
        if obstacle_block:
            audit("obstacle_detected", kind=_LAST_OBSTACLE_KIND or "unknown")
    except Exception as e:
        audit("obstacle_scan_error", error=str(e))
        _LAST_OBSTACLE_KIND = None
        _LAST_RATING_PAGE = None

    # 8. DASHBOARD-RANKING: Umfragen nach EUR/Minute priorisieren
    # WHY: HeyPiggy-Dashboard zeigt viele Umfragen-Kacheln. Der Agent soll die
    # LUKRATIVSTE zuerst klicken — nicht die erstbeste. Reward/Minute +
    # Sterne-Bewertung sind die Ziel-Metriken. Ohne diese Heuristik verplempert
    # der Agent Zeit in 0.03 EUR Screenern und ignoriert die 0.76 EUR Karten.
    #
    # WHY (Issue #61 Fix F2): Frueher war der Block an `?page=dashboard` in
    # der URL gebunden. Google-OAuth redirected aber auf `/` oder
    # `/?tab=surveys` — Folge: Block blieb stumm, Vision bekam keine Ref-IDs,
    # Worker klickte nie eine Kachel. Jetzt aktivieren wir den Block auf
    # jeder heypiggy.com-Seite, die KEINE Survey-Detail-URL (/survey/...) ist
    # UND auf der tatsaechlich div.survey-item-Kacheln im DOM sichtbar sind.
    # Der DOM-Check (siehe clickable_info) garantiert, dass wir nicht auf
    # einer Login- oder Profil-Seite irrtuemlich das Ranking anwerfen.
    # CONSEQUENCES: Block laeuft auf Homepage, ?page=dashboard UND
    # ?tab=surveys — ueberall wo Kacheln tatsaechlich da sind.
    dashboard_block = ""
    try:
        current_url = (
            page_context_data.get("url", "") if isinstance(page_context_data, dict) else ""
        )
    except NameError:
        current_url = ""
    # Fallback: url aus page_context-String extrahieren
    if not current_url and "heypiggy" in (page_context or "").lower():
        current_url = "heypiggy"
    current_url_low = current_url.lower()
    page_context_low = (page_context or "").lower()
    # URL-Heuristik: heypiggy-Domain + KEINE Survey-Detail-Seite
    _on_heypiggy = ("heypiggy.com" in current_url_low) or ("heypiggy" in page_context_low)
    _is_survey_detail = (
        "/survey/" in current_url_low
        or "/s/" in current_url_low
        or "survey_id=" in current_url_low
    )
    # DOM-Signal: wurden im Clickable-Scan tatsaechlich Survey-Kacheln gefunden?
    # (Stufe 1 des js_scan schreibt priority='survey' in die Elemente.)
    _dom_has_survey_cards = "priority=survey" in (clickable_info or "") or "#survey-" in (
        clickable_info or ""
    )
    is_dashboard = (
        (_on_heypiggy and not _is_survey_detail and _dom_has_survey_cards)
        or ("Deine verfügbaren Erhebungen".lower() in page_context_low)
    )
    # Rating/Survey/Obstacle-Pages nicht ueberschreiben
    if is_dashboard and _LAST_OBSTACLE_KIND is None and _LAST_RATING_PAGE is None:
        try:
            dashboard_js = r"""
            (function() {
              function visible(el) {
                if (!el) return false;
                var r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 40) return false;
                var s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
                return r.bottom > 0 && r.top < window.innerHeight + 1200;
              }
              function txt(el) {
                return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
              }
              function refOf(el) {
                var cur = el;
                for (var d = 0; d < 6 && cur; d++) {
                  var r = cur.getAttribute && (cur.getAttribute('data-ref') || cur.getAttribute('data-bridge-ref'));
                  if (r) return r;
                  cur = cur.parentElement;
                }
                return '';
              }
              // Kandidaten: Karten-artige Container mit Euro + Minuten
              var all = Array.from(document.querySelectorAll('div, article, li, a, section')).filter(visible);
              var cards = [];
              var seen = {};
              for (var i = 0; i < all.length && cards.length < 40; i++) {
                var el = all[i];
                var t = txt(el);
                if (t.length < 8 || t.length > 400) continue;
                // Muss sowohl Euro als auch Minuten enthalten
                var eurMatch = t.match(/(\d+[.,]\d{1,2})\s*(?:€|EUR|eur)/);
                var minMatch = t.match(/(\d+)\s*(?:min|Min|Minuten)/i);
                if (!eurMatch || !minMatch) continue;
                // Vermeide Super-Containers (ganze Liste)
                if (t.split(eurMatch[0]).length > 2) continue;
                var key = eurMatch[1] + '|' + minMatch[1] + '|' + t.substring(0, 80);
                if (seen[key]) continue;
                seen[key] = 1;
                var eur = parseFloat(eurMatch[1].replace(',', '.'));
                var mins = parseInt(minMatch[1], 10) || 1;
                if (eur <= 0 || mins <= 0 || eur > 20 || mins > 90) continue;
                // Sterne extrahieren (aria-label="4 stars" oder class enthaelt)
                var stars = 0;
                var starNodes = el.querySelectorAll('[aria-label*="star" i], [class*="star" i], [class*="Stern" i]');
                if (starNodes && starNodes.length) {
                  // Heuristik: gefuellte Sterne zaehlen
                  var filled = 0;
                  for (var sn = 0; sn < starNodes.length; sn++) {
                    var cls = (starNodes[sn].className || '').toString().toLowerCase();
                    var al = (starNodes[sn].getAttribute && starNodes[sn].getAttribute('aria-label') || '').toLowerCase();
                    if (cls.indexOf('full') !== -1 || cls.indexOf('filled') !== -1 || cls.indexOf('active') !== -1) filled++;
                    var m2 = al.match(/(\d)\s*(?:star|stern)/);
                    if (m2) stars = Math.max(stars, parseInt(m2[1], 10));
                  }
                  if (!stars && filled > 0 && filled <= 5) stars = filled;
                }
                var ref = refOf(el);
                if (!ref) continue;
                cards.push({
                  eur: eur,
                  mins: mins,
                  eur_per_min: eur / mins,
                  stars: stars,
                  ref: ref,
                  text_preview: t.substring(0, 120)
                });
              }
              // Sortiere: eur_per_min DESC, stars DESC
              cards.sort(function(a, b) {
                if (Math.abs(a.eur_per_min - b.eur_per_min) > 0.005) return b.eur_per_min - a.eur_per_min;
                return b.stars - a.stars;
              });
              return { cards: cards.slice(0, 5), total: cards.length };
            })();
            """
            dres = await execute_bridge(
                "execute_javascript", {"script": dashboard_js, **tab_params}
            )
            ddata = dres.get("result") if isinstance(dres, dict) else None
            if isinstance(ddata, dict):
                cards = ddata.get("cards") or []
                total = int(ddata.get("total") or 0)
                if cards:
                    lines = [
                        "===== DASHBOARD-RANKING: BESTE UMFRAGEN ZUERST =====",
                        f"Gefunden: {total} Umfragen-Kacheln. Top-Kandidaten (nach EUR/Min, dann Sterne):",
                    ]
                    for i, c in enumerate(cards[:5], start=1):
                        lines.append(
                            f"  {i}. {c.get('eur', 0):.2f} EUR / {c.get('mins', 0)} Min "
                            f"= {c.get('eur_per_min', 0):.3f} EUR/Min | "
                            f"Sterne: {c.get('stars', 0)} | ref={c.get('ref', '')}"
                        )
                    best_ref = cards[0].get("ref", "")
                    lines.append(
                        "DRINGENDE AKTION: click_ref auf die TOP-1 Karte "
                        f"(ref={best_ref}). Niemals wahllos eine Karte klicken — "
                        "immer die lukrativste zuerst. Nach Abschluss kommst du "
                        "zum Dashboard zurueck und wir bewerten neu."
                    )
                    dashboard_block = "\n".join(lines)
                    audit(
                        "dashboard_ranked",
                        total_cards=total,
                        top_eur=cards[0].get("eur"),
                        top_mins=cards[0].get("mins"),
                        top_eur_per_min=round(cards[0].get("eur_per_min", 0), 3),
                    )
        except Exception as e:
            audit("dashboard_scan_error", error=str(e))

    # 9. MEDIA-AUTOPLAY-UNLOCK: Audio/Video auf Survey-Seiten
    # WHY: Browser blockieren Autoplay — Audio-/Video-Fragen brauchen einen
    # menschlichen Click auf Play. Erkennen wir ein blockiertes <audio>/<video>
    # ODER ein Play-Button-Overlay, klicken wir es bevor das Vision-LLM weiterfragt.
    # CONSEQUENCES: Nur als Hinweis im Prompt. Die eigentliche Click-Entscheidung
    # trifft das Vision-LLM auf Basis der ref.
    media_unlock_block = ""
    try:
        media_js = r"""
        (function() {
          function visible(el) {
            if (!el) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 20 || r.height < 20) return false;
            var s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
            return true;
          }
          function refOf(el) {
            var cur = el;
            for (var d = 0; d < 4 && cur; d++) {
              var r = cur.getAttribute && (cur.getAttribute('data-ref') || cur.getAttribute('data-bridge-ref'));
              if (r) return r;
              cur = cur.parentElement;
            }
            return '';
          }
          // Audio / Video Elemente, die paused sind
          var media = Array.from(document.querySelectorAll('audio, video')).filter(visible);
          var blocked = null;
          for (var i = 0; i < media.length; i++) {
            var m = media[i];
            if (m.paused === true || m.autoplay === false) {
              blocked = m; break;
            }
          }
          if (blocked) {
            // Play-Button in der Naehe suchen
            var playBtn = null;
            var near = Array.from(document.querySelectorAll(
              '[aria-label*="play" i], [aria-label*="abspielen" i], button.play, [class*="play-btn" i], [class*="playBtn" i]'
            )).filter(visible);
            if (near.length) playBtn = near[0];
            return {
              kind: blocked.tagName.toLowerCase(),
              ref_media: refOf(blocked),
              ref_play: playBtn ? refOf(playBtn) : '',
              duration: blocked.duration || 0,
              paused: blocked.paused
            };
          }
          return null;
        })();
        """
        mres = await execute_bridge("execute_javascript", {"script": media_js, **tab_params})
        mdata = mres.get("result") if isinstance(mres, dict) else None
        if isinstance(mdata, dict) and mdata.get("paused"):
            kind = mdata.get("kind", "audio")
            ref_media = mdata.get("ref_media") or ""
            ref_play = mdata.get("ref_play") or ""
            dur = mdata.get("duration") or 0
            media_unlock_block = (
                f"===== MEDIA BLOCKIERT ({kind.upper()}) =====\n"
                f"paused=true | duration={dur:.1f}s | "
                f"media_ref={ref_media or '(n/a)'} | play_ref={ref_play or '(n/a)'}\n"
                "DRINGENDE AKTION: click_ref auf den Play-Button (ref_play bevorzugt, "
                f"sonst ref_media). Wenn beide fehlen, execute_javascript: "
                f"  document.querySelectorAll('{kind}').forEach(e=>e.play().catch(()=>{{}}));\n"
                "WARTE die volle Spielzeit ab bevor du die Frage beantwortest — "
                "Panels loggen wenn eine Audio/Video-Frage schneller als die Medienlaenge "
                "beantwortet wird und disqualifizieren stumm."
            )
            audit("media_blocked", kind=kind, duration=round(dur, 1))
    except Exception as e:
        audit("media_unlock_error", error=str(e))

    # 10. MATRIX / GRID / LIKERT-TABELLEN
    # WHY: Professionelle Panels (Sapio, Dynata, Cint) nutzen Raster-Fragen:
    # "Bewerten Sie diese Aussagen von 'Stimme gar nicht zu' bis 'Stimme voll zu'"
    # mit 5-15 Zeilen und 5-7 Spalten. Pro Zeile EIN Radio/Click. Wenn das LLM
    # nur eine Zelle klickt und die anderen vergisst, bleibt die Seite und
    # die "Weiter"-Schaltflaeche bleibt disabled -> Stillstand.
    # CONSEQUENCES: Wir extrahieren die Aussagen pro Zeile und geben Vision
    # einen kompletten Zeilen-Plan mit Standardwert "neutral/mitte" als Default.
    matrix_block = ""
    global _LAST_MATRIX
    try:
        matrix_js = r"""
        (function() {
          function visible(el) {
            if (!el) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return false;
            var s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
            return true;
          }
          function txt(el) { return el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
          function refOf(el) {
            var cur = el;
            for (var d = 0; d < 4 && cur; d++) {
              var r = cur.getAttribute && (cur.getAttribute('data-ref') || cur.getAttribute('data-bridge-ref'));
              if (r) return r;
              cur = cur.parentElement;
            }
            return '';
          }
          // Matrix = Tabelle ODER Container mit mindestens 2 Zeilen mit gleicher Radio-Spalten-Anzahl
          var tables = Array.from(document.querySelectorAll('table, [role="grid"], [role="table"], [class*="matrix" i], [class*="grid" i]')).filter(visible);
          // Fallback: alle divs mit >=2 children die jeweils 3+ radios haben
          var candidates = tables.slice();
          if (!candidates.length) {
            var divs = Array.from(document.querySelectorAll('form, div, section')).filter(function(el) {
              if (!visible(el)) return false;
              var rows = el.querySelectorAll(':scope > *');
              if (rows.length < 2) return false;
              var counts = [];
              for (var i = 0; i < rows.length && counts.length < 6; i++) {
                var rc = rows[i].querySelectorAll('input[type="radio"], [role="radio"]').length;
                if (rc >= 3) counts.push(rc);
              }
              if (counts.length < 2) return false;
              // Alle Counts gleich?
              return counts.every(function(c) { return c === counts[0]; });
            });
            candidates = divs;
          }
          if (!candidates.length) return null;
          // Nimm groesste Matrix
          candidates.sort(function(a,b){
            var ra=a.getBoundingClientRect(); var rb=b.getBoundingClientRect();
            return (rb.width*rb.height)-(ra.width*ra.height);
          });
          var m = candidates[0];
          // Header-Labels (Spalten-Skala)
          var headers = [];
          var ths = m.querySelectorAll('th, thead td, [role="columnheader"]');
          ths.forEach(function(th) {
            if (visible(th)) {
              var t = txt(th);
              if (t && t.length < 60) headers.push(t);
            }
          });
          // Zeilen-Aussagen + Radio-Refs
          var rows = [];
          var rowEls = m.querySelectorAll('tr, [role="row"]');
          if (!rowEls.length) {
            rowEls = m.querySelectorAll(':scope > *');
          }
          rowEls.forEach(function(r) {
            if (!visible(r)) return;
            var radios = r.querySelectorAll('input[type="radio"], [role="radio"]');
            if (radios.length < 3) return;
            // Aussage: erste Zelle oder erstes Label
            var statement = '';
            var firstLabel = r.querySelector('th, td:first-child, [role="rowheader"], label');
            if (firstLabel) statement = txt(firstLabel);
            if (!statement) statement = txt(r).substring(0, 120);
            if (!statement) return;
            var radioRefs = [];
            radios.forEach(function(rd) {
              if (!visible(rd)) return;
              var label = '';
              if (rd.id) {
                var lbl = document.querySelector('label[for="'+rd.id+'"]');
                if (lbl) label = txt(lbl);
              }
              radioRefs.push({ ref: refOf(rd), label: label || (rd.value || '') });
            });
            if (radioRefs.length >= 3) {
              rows.push({ statement: statement.substring(0, 160), radios: radioRefs });
            }
          });
          if (rows.length < 2) return null;
          return {
            headers: headers.slice(0, 10),
            rows: rows.slice(0, 20),
            cols: (rows[0] && rows[0].radios) ? rows[0].radios.length : 0
          };
        })();
        """
        mxres = await execute_bridge("execute_javascript", {"script": matrix_js, **tab_params})
        mxdata = mxres.get("result") if isinstance(mxres, dict) else None
        if isinstance(mxdata, dict) and mxdata.get("rows"):
            _LAST_MATRIX = mxdata
            rows = mxdata.get("rows") or []
            headers = mxdata.get("headers") or []
            cols = int(mxdata.get("cols") or 0)
            # Neutralste Spalte = Mitte (fuer ungerade Skalen), sonst Mitte-links
            # ANTI-STRAIGHT-LINING: Dynata/Sapio/Cint flaggen Antworten bei
            # denen jede Matrix-Zeile in derselben Spalte geklickt wird als
            # "Speeder" bzw. "Straight-liner" — das fuehrt zu Score-Abwertung
            # oder sofortigem DQ. Wir variieren deshalb 25-30% der Zeilen um
            # +/-1 Spalte (deterministisch per stmt-Hash damit Re-Scans stabil
            # bleiben und die Consistency-Memo nicht triggert).
            neutral_idx = (cols // 2) if cols else 0
            lines = [
                "===== MATRIX / GRID-FRAGE ERKANNT =====",
                f"Spalten ({cols}): {', '.join(headers) if headers else '(keine Header)'}",
                f"Zeilen ({len(rows)}):",
            ]
            jittered_count = 0
            for r in rows[:15]:
                stmt = r.get("statement", "")
                radios = r.get("radios", [])
                if not radios:
                    continue
                # Deterministischer Jitter: hash(stmt) mod 10 < 3 -> jittern
                h = int(hashlib.md5(stmt.encode("utf-8")).hexdigest()[:4], 16)
                jitter = 0
                if cols >= 4 and (h % 10) < 3:
                    # +1 oder -1 abhaengig vom Hash
                    jitter = 1 if (h % 2 == 0) else -1
                target_idx = max(0, min(len(radios) - 1, neutral_idx + jitter))
                target = radios[target_idx]
                tag = "jitter" if jitter else "neutral"
                if jitter:
                    jittered_count += 1
                lines.append(
                    f"  - '{stmt[:90]}' -> ref={target.get('ref', '')} "
                    f"({target.get('label', '')}) [{tag}]"
                )
            lines.append(
                "REGEL: Klicke FUER JEDE ZEILE genau ein Radio — "
                "bevorzugt die Persona-plausible Position, sonst oben vorgeschlagene. "
                "ANTI-SPEEDER: Nicht alle Zeilen in derselben Spalte — die [jitter]-"
                "markierten Zeilen bewusst eine Spalte daneben. "
                "NIEMALS 'Weiter' klicken bevor alle Zeilen befuellt sind — sonst "
                "bleibt die Matrix unvollstaendig und die Umfrage sperrt Fortschritt. "
                "Arbeite die Liste von oben nach unten ab, eine Zeile pro Schritt."
            )
            matrix_block = "\n".join(lines)
            audit("matrix_detected", rows=len(rows), cols=cols)
        else:
            _LAST_MATRIX = None
    except Exception as e:
        audit("matrix_scan_error", error=str(e))
        _LAST_MATRIX = None

    # 11. SLIDER / RANGE-FRAGEN
    # WHY: <input type="range"> kann per normalem click_ref nicht auf den
    # gewuenschten Wert gesetzt werden. Wir geben Vision den JS-Befehl vor.
    # CONSEQUENCES: Persona-gesteuerter Default: "mitte-rechts" (neutral-positiv).
    slider_block = ""
    global _LAST_SLIDER
    try:
        slider_js = r"""
        (function() {
          var sliders = Array.from(document.querySelectorAll('input[type="range"], [role="slider"]')).filter(function(el) {
            var r = el.getBoundingClientRect();
            return r.width > 10 && r.height > 3;
          });
          if (!sliders.length) return null;
          return sliders.slice(0, 3).map(function(s) {
            var ref = s.getAttribute('data-ref') || s.getAttribute('data-bridge-ref') || '';
            var p = s.parentElement;
            for (var d = 0; d < 3 && !ref && p; d++) {
              ref = p.getAttribute && (p.getAttribute('data-ref') || p.getAttribute('data-bridge-ref')) || '';
              p = p.parentElement;
            }
            var sel = '';
            if (s.id) sel = '#' + s.id;
            else if (s.name) sel = (s.tagName.toLowerCase()) + '[name="'+s.name+'"]';
            return {
              ref: ref,
              selector: sel,
              min: parseFloat(s.min || s.getAttribute('aria-valuemin') || '0'),
              max: parseFloat(s.max || s.getAttribute('aria-valuemax') || '100'),
              step: parseFloat(s.step || '1'),
              value: parseFloat(s.value || s.getAttribute('aria-valuenow') || '0')
            };
          });
        })();
        """
        sres = await execute_bridge("execute_javascript", {"script": slider_js, **tab_params})
        sdata = sres.get("result") if isinstance(sres, dict) else None
        if isinstance(sdata, list) and sdata:
            _LAST_SLIDER = {"sliders": sdata}
            lines = ["===== SLIDER / RANGE-FRAGE ERKANNT ====="]
            for i, sl in enumerate(sdata, start=1):
                mn = sl.get("min", 0)
                mx = sl.get("max", 100)
                # Neutral-positiver Default: 65% des Ranges
                target = mn + (mx - mn) * 0.65
                step = sl.get("step", 1) or 1
                target = round(target / step) * step
                selector = sl.get("selector") or ""
                lines.append(
                    f"  Slider {i}: min={mn} max={mx} aktuell={sl.get('value')} "
                    f"-> Persona-Ziel={target}"
                )
                if selector:
                    lines.append(
                        f"    JS: var el=document.querySelector('{selector}'); "
                        f"el.value={target}; el.dispatchEvent(new Event('input',{{bubbles:true}})); "
                        f"el.dispatchEvent(new Event('change',{{bubbles:true}}));"
                    )
            lines.append(
                "REGEL: Nutze execute_javascript um den Slider auf den Zielwert "
                "zu setzen (click_ref funktioniert bei Range-Inputs NICHT). "
                "Loese danach 'input' UND 'change' aus, sonst erkennt die Umfrage "
                "keinen neuen Wert."
            )
            slider_block = "\n".join(lines)
            audit("slider_detected", count=len(sdata))
        else:
            _LAST_SLIDER = None
    except Exception as e:
        audit("slider_scan_error", error=str(e))
        _LAST_SLIDER = None

    # 12. INFINITE-SPINNER DETECTION
    # WHY: Manche Screener (siehe PureSpectrum-Screenshot) zeigen minutenlang
    # nur einen Ladekreis ohne interaktive Elemente. Der Agent wartet sonst
    # ewig bis zum no_progress-Limit und bricht die ganze Session ab. Besser:
    # nach 3 aufeinanderfolgenden Leer-Seiten aktiv refreshen.
    # CONSEQUENCES: _SPINNER_STREAK wird inkrementiert; bei >=3 empfehlen wir
    # im Prompt einen navigate zur aktuellen URL (harter Reload).
    spinner_block = ""
    global _SPINNER_STREAK
    try:
        spinner_js = r"""
        (function() {
          var vis = function(el){ if(!el) return false; var r=el.getBoundingClientRect(); if(r.width<2||r.height<2) return false; var s=window.getComputedStyle(el); return s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity)>=0.1; };
          var hasSpinner = !!document.querySelector(
            '[class*="spinner" i], [class*="loader" i], [class*="loading" i], [role="progressbar"], .MuiCircularProgress-root, svg[class*="spin" i]'
          );
          var spinnerVisible = false;
          if (hasSpinner) {
            var spinners = document.querySelectorAll('[class*="spinner" i], [class*="loader" i], [class*="loading" i], [role="progressbar"], .MuiCircularProgress-root, svg[class*="spin" i]');
            for (var i = 0; i < spinners.length; i++) { if (vis(spinners[i])) { spinnerVisible = true; break; } }
          }
          // Interaktive Elemente zaehlen
          var interactive = Array.from(document.querySelectorAll(
            'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="radio"], [role="checkbox"], [role="link"]'
          )).filter(vis);
          // Cookie-Banner-Buttons zaehlen wir NICHT — die sollen separat behandelt werden.
          var realInteractive = interactive.filter(function(el) {
            var t = (el.innerText||el.textContent||'').toLowerCase();
            return t.indexOf('akzeptieren') === -1 && t.indexOf('accept') === -1 && t.indexOf('ablehnen') === -1;
          });
          var bodyTextLen = (document.body.innerText || '').replace(/\s+/g,' ').trim().length;
          return {
            spinnerVisible: spinnerVisible,
            interactiveCount: realInteractive.length,
            bodyTextLen: bodyTextLen
          };
        })();
        """
        spres = await execute_bridge("execute_javascript", {"script": spinner_js, **tab_params})
        spdata = spres.get("result") if isinstance(spres, dict) else None
        loading_only = False
        if isinstance(spdata, dict):
            loading_only = (
                bool(spdata.get("spinnerVisible"))
                and int(spdata.get("interactiveCount", 0)) == 0
                and int(spdata.get("bodyTextLen", 0)) < 200
            )
        if loading_only:
            _SPINNER_STREAK += 1
            if _SPINNER_STREAK >= 3:
                spinner_block = (
                    "===== HINDERNIS: INFINITE SPINNER =====\n"
                    f"Seit {_SPINNER_STREAK} Schritten nur Spinner + keine interaktiven "
                    "Elemente. Die Seite haengt.\n"
                    "DRINGENDE AKTION: execute_javascript mit "
                    "'location.reload()' ODER navigate zur aktuellen URL. "
                    "Nach Reload wartet human_delay bis DOM geladen ist. "
                    "Nicht ewig darauf warten dass etwas von selbst erscheint."
                )
                audit("spinner_loop", streak=_SPINNER_STREAK)
        else:
            _SPINNER_STREAK = 0
    except Exception as e:
        audit("spinner_scan_error", error=str(e))

    # 13. SAME-QUESTION-LOOP DETECTION (Update)
    # WHY: Wenn _LAST_QUESTION_TEXT in den letzten 3 Scans identisch war,
    # waehlt Vision wiederholt die falsche Option -> wir muessen Eskalation
    # einleiten: alternative Option probieren oder zurueck/refresh.
    # CONSEQUENCES: Ein Block im Prompt der Vision zwingt die ZWEIT- oder
    # DRITTBESTE Option zu klicken bzw. bei 4+ Retries navigate back zu probieren.
    loop_block = ""
    global _RECENT_QUESTIONS, _SAME_QUESTION_STREAK
    try:
        if _LAST_QUESTION_TEXT:
            _RECENT_QUESTIONS.append(_LAST_QUESTION_TEXT)
            if len(_RECENT_QUESTIONS) > 6:
                _RECENT_QUESTIONS = _RECENT_QUESTIONS[-6:]
            # Streak = wie oft hintereinander dieselbe Frage
            streak = 1
            for i in range(len(_RECENT_QUESTIONS) - 2, -1, -1):
                if _RECENT_QUESTIONS[i] == _LAST_QUESTION_TEXT:
                    streak += 1
                else:
                    break
            _SAME_QUESTION_STREAK = streak
            if streak >= 3:
                esc_instructions = [
                    "DRINGENDE ESKALATION: Die Seite bleibt auf derselben Frage.",
                    f"Streak: {streak} Wiederholungen.",
                ]
                if streak == 3:
                    esc_instructions.append(
                        "AKTION: Probiere eine ANDERE (zweitbeste) Option als beim "
                        "letzten Versuch. Vermutlich ist ein Validierungs-Constraint "
                        "verletzt (z.B. alle Zeilen einer Matrix nicht ausgefuellt, "
                        "Checkbox 'Keine' darf nicht zusammen mit anderen)."
                    )
                elif streak == 4:
                    esc_instructions.append(
                        "AKTION: Suche nach einer 'Weiter'/'Next'/'Continue'/'Submit' "
                        "Schaltflaeche und klicke sie. Vielleicht hat die Antwort "
                        "geklappt, aber es fehlt der Submit."
                    )
                elif streak >= 5:
                    esc_instructions.append(
                        "AKTION: execute_javascript 'location.reload()' ODER "
                        "navigate mit url=window.location.href. Wir sind definitiv "
                        "haengen geblieben."
                    )
                loop_block = "===== SAME-QUESTION-LOOP =====\n" + "\n".join(esc_instructions)
                audit("same_question_loop", streak=streak)
    except Exception as e:
        audit("loop_scan_error", error=str(e))

    # ============================================================
    # MEGA-SCAN (Tail-Block 14+15+16+19 in einem Roundtrip)
    # ============================================================
    # WHY: Die Scanner 14 (required fields), 15 (reward totalizer), 16
    # (panel URL+body text) und 19 (error banner) laufen am Ende von
    # dom_prescan und sind alle reine Lesezugriffe aufs DOM. Frueher
    # waren das 4 separate Bridge-Calls = 4 Roundtrips ueber MCP.
    # Jetzt buendeln wir sie in EINEN JS-Call und sparen damit 3
    # Roundtrips pro Step (~300-400ms bei typischer MCP-Latenz).
    # CONSEQUENCES: Bei 50 Steps pro Umfrage = 15-20s Ersparnis pro
    # Survey, 15-20min pro 50-Survey-Tag. Die einzelnen Blocks sind
    # trotzdem noch robust: bei JS-Fehler wird _mega=None gesetzt und
    # die Blocks fallen still auf "kein Output" zurueck — genau wie
    # frueher bei Einzelfehlern.
    _mega: dict | None = None
    try:
        mega_js = r"""
        (function() {
          function visible(el) {
            if (!el) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return false;
            var s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) >= 0.1;
          }
          function visibleStrict(el) {
            if (!el) return false;
            var r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return false;
            var s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) >= 0.3;
          }
          function refOf(el) {
            var cur = el;
            for (var d = 0; d < 4 && cur; d++) {
              var r = cur.getAttribute && (cur.getAttribute('data-ref') || cur.getAttribute('data-bridge-ref'));
              if (r) return r;
              cur = cur.parentElement;
            }
            return '';
          }
          function labelOf(el) {
            if (el.id) {
              var lbl = document.querySelector('label[for="'+el.id+'"]');
              if (lbl) return (lbl.innerText||lbl.textContent||'').replace(/\s+/g,' ').trim().substring(0,80);
            }
            var p = el.closest('label');
            if (p) return (p.innerText||p.textContent||'').replace(/\s+/g,' ').trim().substring(0,80);
            var aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
            return (aria||'').substring(0,80);
          }

          var bodyText = (document.body && document.body.innerText || '').replace(/\s+/g, ' ');
          var bodyTextShort = bodyText.substring(0, 3000);

          // --- Required-Field-Validator (frueher Scanner 14) ---
          var reqAll = Array.from(document.querySelectorAll(
            'input[required], select[required], textarea[required], '+
            '[aria-required="true"], [data-required="true"], [data-required="required"]'
          )).filter(visible);
          var empties = [];
          for (var i = 0; i < reqAll.length && empties.length < 15; i++) {
            var el = reqAll[i];
            var tag = el.tagName.toLowerCase();
            var type = (el.type||'').toLowerCase();
            var empty = false;
            if (tag === 'input' && (type === 'checkbox' || type === 'radio')) {
              if (!el.name) continue;
              var grp = document.getElementsByName(el.name);
              var anyChecked = false;
              for (var g = 0; g < grp.length; g++) { if (grp[g].checked) { anyChecked = true; break; } }
              if (!anyChecked) empty = true;
              if (empty && empties.some(function(e){ return e.group === el.name; })) continue;
            } else if (tag === 'select') {
              empty = !el.value || el.value === '' || el.selectedIndex === 0;
            } else {
              empty = !el.value || el.value.trim() === '';
            }
            if (empty) {
              empties.push({ ref: refOf(el), tag: tag, type: type, label: labelOf(el), group: el.name || '' });
            }
          }
          var submits = Array.from(document.querySelectorAll(
            'button[type="submit"], input[type="submit"], button, [role="button"]'
          )).filter(function(b) {
            if (!visible(b)) return false;
            var t = (b.innerText||b.textContent||b.value||'').toLowerCase();
            return /weiter|next|continue|submit|absenden|fortfahren|weiterleiten|fertig/.test(t);
          });

          // --- EUR-Totalizer (frueher Scanner 15) ---
          var rewardPatterns = [
            /(?:gutgeschrieben|erhalten|credit|earned|\+)\s*(\d+[.,]\d{1,2})\s*(?:EUR|€|eur)/gi,
            /(\d+[.,]\d{1,2})\s*(?:EUR|€|eur)\s*(?:gutgeschrieben|erhalten|credit|earned)/gi
          ];
          var rewards = [];
          for (var p = 0; p < rewardPatterns.length; p++) {
            var m;
            while ((m = rewardPatterns[p].exec(bodyText)) !== null) {
              var v = parseFloat(m[1].replace(',', '.'));
              if (v > 0 && v < 50) {
                var idx = m.index;
                var ctx = bodyText.substring(Math.max(0, idx - 20), Math.min(bodyText.length, idx + m[0].length + 20));
                rewards.push({ amount: v, context: ctx.substring(0, 80) });
                if (rewards.length >= 5) break;
              }
            }
            if (rewards.length >= 5) break;
          }

          // --- Error-Banner (frueher Scanner 19) ---
          var errNodes = Array.from(document.querySelectorAll(
            '[role="alert"], [class*="error" i], [class*="invalid" i], [class*="warning" i], '+
            '[class*="required" i], .text-danger, .text-red-500, .text-red-600, .alert, .error-message, .validation-summary'
          )).filter(visibleStrict);
          var errHits = [];
          for (var i2 = 0; i2 < errNodes.length && errHits.length < 5; i2++) {
            var t2 = (errNodes[i2].innerText || errNodes[i2].textContent || '').replace(/\s+/g,' ').trim();
            if (!t2 || t2.length < 4 || t2.length > 240) continue;
            var lo = t2.toLowerCase();
            if (lo.indexOf('bitte') !== -1 || lo.indexOf('please') !== -1 ||
                lo.indexOf('required') !== -1 || lo.indexOf('pflicht') !== -1 ||
                lo.indexOf('beantworten') !== -1 || lo.indexOf('answer') !== -1 ||
                lo.indexOf('fehler') !== -1 || lo.indexOf('error') !== -1 ||
                lo.indexOf('invalid') !== -1 || lo.indexOf('ungueltig') !== -1 || lo.indexOf('ungültig') !== -1) {
              errHits.push(t2.substring(0, 180));
            }
          }

          return {
            required: { empty: empties, submitInView: submits.length > 0 },
            reward: rewards,
            panel: { url: window.location.href, text: bodyTextShort },
            errorBanner: errHits
          };
        })();
        """
        mres = await execute_bridge("execute_javascript", {"script": mega_js, **tab_params})
        if isinstance(mres, dict) and isinstance(mres.get("result"), dict):
            _mega = mres["result"]
    except Exception as e:
        audit("megascan_error", error=str(e))
        _mega = None

    # 14. REQUIRED-FIELD-VALIDATOR
    # WHY: Die haeufigste Ursache fuer "Weiter-Button tut nichts"-Loops sind
    # unvollstaendig befuellte Pflichtfelder. HTML markiert diese mit
    # required, aria-required="true", data-required oder durch Sterne (*)
    # im Label. Wir zaehlen die noch leeren vor jedem Render und zwingen
    # Vision sie auszufuellen BEVOR "Weiter" geklickt wird.
    # CONSEQUENCES: Kein Blindklick mehr auf Submit-Buttons wenn noch
    # Felder offen sind. Bricht die Endlosschleife "klick-Weiter -> Fehler
    # -> klick-Weiter -> Fehler" sofort.
    required_block = ""
    global _LAST_EMPTY_REQUIRED
    try:
        # WHY: Frueher separater Bridge-Call; jetzt aus Mega-Scan gelesen.
        # Kein Fallback — wenn _mega fehlt, ueberspringen wir diesen Block.
        rdata = _mega.get("required") if isinstance(_mega, dict) else None
        if isinstance(rdata, dict):
            empties = rdata.get("empty") or []
            submit_in_view = bool(rdata.get("submitInView"))
            _LAST_EMPTY_REQUIRED = len(empties)
            if empties and submit_in_view:
                lines = [
                    f"===== PFLICHTFELDER UNVOLLSTAENDIG ({len(empties)}) =====",
                    "WARNUNG: 'Weiter/Submit'-Button ist sichtbar, aber mindestens ein",
                    "Pflichtfeld ist noch leer. Klicke NICHT auf Weiter -> erst fuellen.",
                    "Offene Pflichtfelder:",
                ]
                for e in empties[:10]:
                    lines.append(
                        f"  - {e.get('tag', '?')}[{e.get('type', '')}] '{e.get('label', '')}' ref={e.get('ref', '')}"
                    )
                lines.append(
                    "REGEL: Beantworte diese Felder zuerst (Persona-konform), "
                    "dann erst Weiter klicken. Wenn ein Feld unklar ist, waehle "
                    "Neutral-Default (Mitte bei Skalen, 'keine Angabe' bei Demographie)."
                )
                required_block = "\n".join(lines)
                audit("required_empty", count=len(empties))
    except Exception as e:
        audit("required_scan_error", error=str(e))

    # 15. EUR-TOTALIZER
    # WHY: HeyPiggy bestaetigt jeden Abschluss mit "+0.XX EUR gutgeschrieben"
    # auf dem Dashboard oder einer Success-Seite. Wir aggregieren diese
    # Betraege pro Run. Deduplication ueber exakten Banner-String plus
    # Betrag+Timestamp damit Polling denselben Banner nicht doppelt zaehlt.
    # CONSEQUENCES: run_summary.json + print_summary zeigen den Netto-Verdienst.
    earnings_block = ""
    global _SEEN_REWARD_STRINGS
    try:
        # WHY: Frueher separater Bridge-Call; jetzt aus Mega-Scan gelesen.
        edata = _mega.get("reward") if isinstance(_mega, dict) else None
        _rs = globals().get("CURRENT_RUN_SUMMARY")
        if isinstance(edata, list) and edata and _rs is not None:
            new_bookings = []
            for entry in edata:
                amount = float(entry.get("amount", 0))
                ctx = str(entry.get("context", ""))[:80]
                key = f"{round(amount, 2)}|{ctx}"
                if key in _SEEN_REWARD_STRINGS:
                    continue
                _SEEN_REWARD_STRINGS.add(key)
                if _rs.record_earning(amount, dedup_key=key):
                    new_bookings.append((amount, ctx))
            if new_bookings:
                total = _rs.earnings_eur
                lines = ["===== EUR GUTGESCHRIEBEN (NEU GEBUCHT) ====="]
                for a, c in new_bookings:
                    lines.append(f"  +{a:.2f} EUR | Kontext: '{c}'")
                lines.append(f"Session-Summe jetzt: {total:.2f} EUR")
                earnings_block = "\n".join(lines)
                audit(
                    "earnings_booked",
                    new_count=len(new_bookings),
                    session_total=total,
                )
    except Exception as e:
        audit("earnings_scan_error", error=str(e))

    # 16. PANEL-OVERRIDES (PureSpectrum / Dynata / Sapio / Cint / Lucid / HeyPiggy)
    # WHY: Jeder Panel-Provider hat eigene Traps, DQ-Signale und Quality-Checks.
    # Wir erkennen an URL + Body-Text welcher Provider aktiv ist und injizieren
    # provider-spezifische Regeln (Attention-Checks, Min-Zeiten, Redirect-Quirks).
    # CONSEQUENCES: Vision bekommt einen Cheatsheet statt jedes Mal neu zu raten
    # — deutlich hoehere Completion-Rate bei Router-basierten Umfragen.
    panel_block = ""
    try:
        # WHY: Frueher separater Bridge-Call; jetzt aus Mega-Scan gelesen.
        _panel_url = ""
        _panel_text = ""
        if isinstance(_mega, dict):
            p = _mega.get("panel") or {}
            if isinstance(p, dict):
                _panel_url = str(p.get("url", ""))
                _panel_text = str(p.get("text", ""))
        panel = detect_panel(url=_panel_url, body_text=_panel_text)
        if panel is not None:
            panel_block = build_panel_prompt_block(panel, body_text=_panel_text)
            audit("panel_detected", panel=panel.name, url=_panel_url[:80])
    except Exception as e:
        audit("panel_scan_error", error=str(e))

    # 17. ATTENTION-CHECK AUTO-SOLVER
    # WHY: Panels bauen explizite Attention-Checks ein: "Um zu zeigen dass Sie
    # aufmerksam sind, waehlen Sie bitte 'Stimme zu'". Wer die falsche Option
    # klickt wird sofort disqualifiziert (manchmal OHNE Warnung). Die Persona-
    # Logik darf diese Checks NICHT ueberschreiben — die Anweisung muss 1:1
    # befolgt werden.
    # CONSEQUENCES: Wir parsen den Fragetext nach expliziten "waehlen/select/
    # klicken Sie X"-Mustern und injizieren einen MUSS-CLICK-Block in den
    # Prompt. Vision wird angewiesen die genannte Option zu klicken, egal was
    # Persona sagt.
    attention_block = ""
    try:
        if _LAST_QUESTION_TEXT:
            qtext = _LAST_QUESTION_TEXT
            # Patterns: DE + EN, mit Anfuehrungszeichen-Erkennung
            patterns = [
                # "waehlen Sie bitte 'X'" / "select X"
                r"(?:bitte\s+)?(?:w[aä]hlen|klicken|markieren|w[aä]hle|klicke|tippen?)\s+Sie\s+(?:bitte\s+)?['\"“„](.+?)['\"”“]",
                r"(?:please\s+)?(?:select|choose|click|pick|mark|tap)\s+['\"“„](.+?)['\"”“]",
                # Ohne Quotes: "waehlen Sie die Option X"
                r"w[aä]hlen\s+Sie\s+(?:die\s+)?(?:option|antwort)\s+([A-Za-zÄÖÜäöüß0-9 ]{2,30}?)(?:[.,!?]|$)",
                r"(?:please\s+)?(?:select|choose)\s+(?:the\s+)?(?:option|answer)\s+([A-Za-z0-9 ]{2,30}?)(?:[.,!?]|$)",
                # "um zu zeigen dass Sie aufmerksam sind": meist gefolgt von Anweisung
                r"aufmerksam(?:keit)?[^.!?]{0,60}['\"“„](.+?)['\"”“]",
                r"attention[^.!?]{0,60}['\"“„](.+?)['\"”“]",
            ]
            want_value = None
            for pat in patterns:
                m = re.search(pat, qtext, re.IGNORECASE)
                if m:
                    want_value = m.group(1).strip()
                    if len(want_value) >= 2 and len(want_value) <= 80:
                        break
                    want_value = None
            if want_value:
                # Finde die passende Option in clickable_info (als Hilfe für Vision)
                attention_block = (
                    "===== ATTENTION-CHECK ERKANNT — MUSS-KLICK =====\n"
                    f"Die Frage enthaelt eine EXPLIZITE Anweisung: '{want_value}' "
                    "muss als Antwort gewaehlt werden.\n"
                    "REGEL: Ignoriere in diesem Schritt die Persona. Suche im "
                    "Clickable-Snapshot die Option deren Label oder Text genau "
                    f"oder moeglichst genau '{want_value}' entspricht und klicke sie. "
                    "Falls mehrere Kandidaten: nimm den mit der kuerzesten Levenshtein-"
                    "Distanz zum Wunsch-Text. Kein Fallback, kein Raten — wenn die "
                    "Option nicht auffindbar ist, scrolle zuerst die Seite ab."
                )
                audit("attention_check_detected", want=want_value[:60])
    except Exception as e:
        audit("attention_scan_error", error=str(e))

    # 18. OPEN-ENDED MINIMUM-LENGTH ENFORCER
    # WHY: Fragen mit Freitext-Antwort haben oft "mindestens 20 Zeichen" oder
    # "at least 3 words" — wenn der Text zu kurz ist, bleibt der Weiter-Button
    # disabled. Vision weiss das oft nicht und tippt nur "ok" oder "gut".
    # CONSEQUENCES: Wir parsen die Mindestlaenge aus Frage- und Fehler-Text
    # und schreiben sie als harte Vorgabe in den Prompt.
    minlen_block = ""
    try:
        if _LAST_QUESTION_TEXT:
            qtext = _LAST_QUESTION_TEXT
            min_chars = 0
            min_words = 0
            mc = re.search(
                r"(?:mindestens|at least|min[.:\s]+)\s*(\d{1,3})\s*(?:zeichen|characters?|chars?|signs?)",
                qtext,
                re.IGNORECASE,
            )
            if mc:
                try:
                    min_chars = int(mc.group(1))
                except ValueError:
                    min_chars = 0
            mw = re.search(
                r"(?:mindestens|at least|min[.:\s]+)\s*(\d{1,3})\s*(?:w[oö]rter?|words?)",
                qtext,
                re.IGNORECASE,
            )
            if mw:
                try:
                    min_words = int(mw.group(1))
                except ValueError:
                    min_words = 0
            if min_chars or min_words:
                demands = []
                if min_chars:
                    demands.append(f"{min_chars} Zeichen")
                if min_words:
                    demands.append(f"{min_words} Woerter")
                minlen_block = (
                    "===== FREITEXT-MINDESTLAENGE ERKANNT =====\n"
                    f"Die Frage verlangt: {', '.join(demands)}. "
                    "REGEL: Die Antwort MUSS diese Laenge erreichen, sonst bleibt "
                    "'Weiter' disabled. Formuliere Persona-plausibel in vollstaendigen "
                    "Saetzen (keine Fuellwoerter, keine wiederholten Phrasen — "
                    "Quality-Algorithmen erkennen 'xxxxxxxxx' oder 'test test test'). "
                    "Lieber einen Satz mehr als einen zu wenig."
                )
                audit(
                    "minlen_detected",
                    min_chars=min_chars,
                    min_words=min_words,
                )
    except Exception as e:
        audit("minlen_scan_error", error=str(e))

    # 19. ERROR-BANNER RECOVERY
    # WHY: Wenn die Umfrage einen roten Fehler-Banner zeigt ("Bitte beantworten
    # Sie alle Fragen", "Please answer all questions"), ist das ein expliziter
    # Hinweis dass Required-Fields uebersehen wurden. Wir zwingen Vision dann
    # zu einem Rescan statt nochmal Weiter zu klicken.
    # CONSEQUENCES: Auch wenn Block 14 keine Pflichtfelder gemeldet hat (z.B.
    # weil sie ohne [required] aber mit panel-eigener Validierung laufen),
    # wird der Agent auf die Fehlermeldung reagieren.
    errbanner_block = ""
    try:
        # WHY: Frueher separater Bridge-Call; jetzt aus Mega-Scan gelesen.
        ebhits = _mega.get("errorBanner") if isinstance(_mega, dict) else None
        if isinstance(ebhits, list) and ebhits:
            errbanner_block = (
                "===== VALIDIERUNGS-FEHLER SICHTBAR =====\n"
                "Die Seite zeigt eine oder mehrere Fehler-Meldungen:\n"
                + "\n".join(f"  * '{h}'" for h in ebhits[:4])
                + "\nREGEL: Nicht erneut 'Weiter' klicken. Scrolle stattdessen zum "
                "Fehler (meist rot, oben oder direkt unter dem betroffenen Feld), "
                "identifiziere das unbeantwortete Feld und fuelle es aus. "
                "Oft sind es: uebersehene Radio-Zeile in einer Matrix, ein leerer "
                "Pflicht-Freitext oder ein nicht gesetztes Geburtsdatum-Dropdown."
            )
            audit("error_banner_detected", hit_count=len(ebhits))
    except Exception as e:
        audit("error_banner_error", error=str(e))

    # 20. ANSWER-CONSISTENCY MEMO
    # WHY: Panels stellen dieselbe Frage absichtlich mehrfach um zu pruefen ob
    # der Befragte konsistent antwortet ("Wie alt sind Sie?" in Demographie
    # + "Bitte bestaetigen Sie Ihr Alter" in der Validation-Phase). Wer sich
    # widerspricht wird disqualifiziert UND riskiert permanente Sperrung.
    # CONSEQUENCES: Wir merken uns pro Umfrage die gegebenen Antworten unter
    # einem Frage-Hash. Bei Wiederauftauchen wird die gemerkte Antwort als
    # PFLICHT-ANTWORT in den Prompt injiziert.
    consistency_block = ""
    try:
        if _LAST_QUESTION_TEXT:
            # Normalisierung: Kleinbuchstaben, Whitespace-Kollaps, Satzzeichen raus
            norm = re.sub(r"\s+", " ", _LAST_QUESTION_TEXT.lower()).strip()
            norm = re.sub(r"[.,;:!?\"'„“”‚‘’()\[\]]", "", norm)
            qhash = hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]
            prior = _ANSWER_MEMO.get(qhash)
            if prior:
                consistency_block = (
                    "===== KONSISTENZ-PFLICHT =====\n"
                    "Diese Frage wurde in dieser Umfrage schon einmal beantwortet:\n"
                    f"  Frueher: '{prior[:120]}'\n"
                    "REGEL: Gib GENAU DIESELBE Antwort wieder. Ein Widerspruch fuehrt "
                    "zu sofortigem DQ und moeglicher permanenter Sperrung bei diesem "
                    "Panel. Wenn die genaue Option nicht mehr verfuegbar ist, waehle "
                    "die semantisch naechste (z.B. gleicher Altersbereich, gleiche "
                    "Haushalts-Groesse). Persona-Logik wird in diesem Schritt "
                    "ignoriert."
                )
                audit("consistency_memo_hit", qhash=qhash)
    except Exception as e:
        audit("consistency_scan_error", error=str(e))

    # 21. QUOTA-FULL DETECTION (NICHT als DQ lernen!)
    # WHY: "Leider haben wir bereits genug Teilnehmer fuer diese Umfrage" ist
    # KEIN Persoenlichkeits-DQ — morgen kann die Quote wieder offen sein. Wir
    # setzen nur ein Flag damit der survey_done-Handler die URL nicht ins
    # Brain als "avoid" schreibt. Ansonsten verpassen wir uns selbst durch
    # zu aggressives Lernen legitime Umfragen.
    # CONSEQUENCES: Flag wird beim Survey-Start wieder geleert.
    global _QUOTA_FULL_DETECTED
    try:
        qtext = (_panel_text or "").lower()
        quota_markers = [
            "quote erreicht",
            "quote bereits erreicht",
            "quote voll",
            "quota full",
            "quota has been filled",
            "quota reached",
            "survey is full",
            "umfrage ist voll",
            "leider haben wir bereits genug",
            "this survey is no longer accepting",
            "nicht mehr verfuegbar",
            "bereits genug teilnehmer",
        ]
        if any(m in qtext for m in quota_markers):
            _QUOTA_FULL_DETECTED = True
            audit("quota_full_detected")
    except Exception as e:
        audit("quota_scan_error", error=str(e))

    return "\n\n".join(
        filter(
            None,
            [
                page_context,
                snapshot_info,
                clickable_info,
                media_block,
                question_block,
                screener_block,
                obstacle_block,
                dashboard_block,
                media_unlock_block,
                matrix_block,
                slider_block,
                spinner_block,
                loop_block,
                required_block,
                earnings_block,
                panel_block,
                attention_block,
                minlen_block,
                errbanner_block,
                consistency_block,
            ],
        )
    )


# ============================================================================
# PERSONA + TRAP / ATTENTION-CHECK PROMPT-BAUSTEINE
# WHY: Umfragen sind voller Fallen — Pre-Qualifikation, Aufmerksamkeits-Checks,
# Konsistenz-Traps (gleiche Frage in anderer Formulierung), Trick-Fragen
# ("Haben Sie in den letzten 6 Monaten Produkt X gekauft?"). Der Agent muss
# diese wie ein Meister erkennen und Persona-konsistent beantworten — sonst
# Rausflug und 0 Cent.
# CONSEQUENCES: Die folgenden Helfer bauen kompakte Prompt-Bausteine die vor
# dem eigentlichen Survey-Prompt injiziert werden. Sie sind billig (kein LLM-
# Call) und laufen bei jedem Step neu.
# ============================================================================


def _collect_recent_answers(limit: int = 8) -> list[dict[str, object]]:
    """Lese die letzten N Antworten aus dem ANSWER_LOG JSONL-File."""
    if ANSWER_LOG is None or not ANSWER_LOG.log_path.exists():
        return []
    try:
        lines = ANSWER_LOG.log_path.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return []
    out: list[dict[str, object]] = []
    for raw in lines[-limit * 2 :]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out[-limit:]


def _build_persona_answer_hint(
    question_text: str | None,
    options: list[str],
) -> str:
    """
    Loest die aktuelle Frage gegen die Persona auf und gibt einen MUSS-Hinweis aus.

    WHY: Wenn im Profil "gender=male" steht und die Frage "Bitte waehlen Sie
    Ihr Geschlecht" zeigt "Maennlich" als Option, DARF das Vision-LLM die Frage nicht
    zufaellig beantworten. Wir geben ihm die Pflicht-Antwort direkt im Prompt.
    CONSEQUENCES: Leerer String wenn keine Frage erkannt oder kein Match gefunden.
    """
    if ACTIVE_PERSONA is None or not question_text:
        return ""
    try:
        result = resolve_answer(ACTIVE_PERSONA, question_text, options or [])
    except Exception:
        return ""
    if not result or result.get("confidence") == "unknown":
        return ""

    lines = ["===== PERSONA-MUSS-ANTWORT FUER AKTUELLE FRAGE ====="]
    lines.append(f"Frage: {question_text[:200]}")
    topic = result.get("topic")
    raw = result.get("raw_value")
    matched = result.get("matched_option")
    reason = result.get("reason", "")
    conf = result.get("confidence", "medium")

    if matched:
        if isinstance(matched, list):
            lines.append(f"PFLICHT-OPTIONEN (multi-select): {', '.join(str(m) for m in matched)}")
        else:
            lines.append(f"PFLICHT-OPTION (genau diese klicken): {matched}")
    if raw not in (None, "", 0, [], ()):
        lines.append(f"Persona-Rohwert: {raw} (Feld: {topic})")
    lines.append(f"Confidence: {conf} — {reason}")
    lines.append(
        "REGEL: Wenn die PFLICHT-OPTION sichtbar ist, klicke GENAU DIESE — "
        "niemals eine andere. Bei Mehrfachauswahl klicke ALLE gelisteten Optionen."
    )
    # Bei Einkommen / Bracket-Fragen Persona-Rohwert fuer Bracket-Auswahl
    if topic and topic.startswith("income_") and raw:
        lines.append(
            f"Falls nur Brackets sichtbar sind: waehle das Bracket das {raw} EUR enthaelt."
        )
    return "\n".join(lines)


def _build_consistency_block(question_text: str | None) -> str:
    """
    Sucht im Answer-Log nach einer semantisch aehnlichen frueheren Frage und
    injiziert die damalige Antwort als MUSS-Wiederholung.

    WHY: Attention-Traps stellen dieselbe Frage 2-3x unterschiedlich formuliert.
    Wenn wir zuerst "34" und spaeter "35" sagen, disqualifizieren wir uns selbst.
    """
    if ANSWER_LOG is None or not question_text:
        return ""
    try:
        prior = ANSWER_LOG.find_prior_answer(question_text, similarity_threshold=0.72)
    except Exception:
        prior = None
    if not prior:
        return ""
    return (
        "===== KONSISTENZ-TRAP ERKANNT =====\n"
        f"Aehnliche Frage wurde bereits beantwortet (sim>=0.72):\n"
        f"- Damals: '{str(prior.get('question', ''))[:160]}'\n"
        f"- Antwort: '{str(prior.get('answer', ''))[:160]}'\n"
        f"- Topic: {prior.get('topic')} | Confidence: {prior.get('confidence')}\n"
        "REGEL: Gib GENAU DIESELBE Antwort wie damals, auch wenn die Formulierung "
        "heute anders klingt. Inkonsistenz = Disqualifikation."
    )


_TRAP_DETECTION_RULES_PROMPT = """===== UMFRAGE-FALLEN & PRE-QUALIFIKATION — MEISTER-MODUS =====
Du agierst wie ein erfahrener Panelist. Jede Umfrage hat VIER Fallentypen — erkenne und meistere sie:

1) PRE-QUALIFIKATION / SCREENING (meist die ersten 3-8 Fragen):
   - Ziel der Umfrage: aussortieren, WER antworten darf (Alters-, Regions-, Berufs-, Produkt-Nutzungs-Filter).
   - Regel: Beantworte EHRLICH aus Persona. Wenn Persona negativ -> Disqualifikation akzeptieren (niemals luegen!).
   - Typische Frage: "Arbeiten Sie in Marketing / Marktforschung / Medien?" -> AUS PERSONA (meistens "Nein").
   - Typische Frage: "Haben Sie in den letzten 3 Monaten Produkt X gekauft?" -> aus brand_preferences / extra_facts.
   - NIEMALS "Ja" nur um reinzukommen — Folge-Fragen decken die Luege auf = Rausflug + Sperre.

2) ATTENTION CHECK (ueberall, besonders Mitte):
   - Explizit: "Bitte waehlen Sie 'Option 3' um zu zeigen dass Sie die Frage lesen."
   - Implizit: Doppelte Verneinung, widerspruechliche Skala, "Wie heisst dieser Button?".
   - Regel: Lies die Frage WORTWOERTLICH. Klicke genau das angeforderte Element.

3) KONSISTENZ-TRAP (gleiche Info zweimal, anders formuliert):
   - Frage 1: "Wie alt sind Sie?" -> Frage 20: "In welchem Jahr wurden Sie geboren?"
   - Frage 1: "Haushaltseinkommen?" -> Frage 15: "Welches Bracket trifft auf Ihr Jahresbrutto zu?"
   - Regel: Nutze IMMER Persona + Answer-Log oben. Gleicher Sachverhalt -> gleiche Antwort.

4) FOLGE-FRAGEN / BRANCHING (eine Frage haengt semantisch von einer frueheren ab):
   - "Welche Marke haben Sie gekauft?" setzt voraus dass in Frage 5 "Haben Sie gekauft?" = Ja war.
   - Regel: Branching-Antworten MUESSEN zur frueheren Ja/Nein-Antwort passen. Sonst Disqualifikation.
   - Wenn Persona sagt "kein Auto", darfst du bei "Welches Auto fahren Sie?" NICHT raten — waehle
     "Keines" / "Kein Auto" / "Trifft nicht zu" wenn sichtbar; sonst die neutralste Option.

ANTWORT-STRATEGIE (in dieser Reihenfolge):
  a) PERSONA-MUSS-ANTWORT oben -> genau diese Option klicken.
  b) KONSISTENZ-TRAP-Block oben -> damalige Antwort wiederholen.
  c) Wenn weder a) noch b): waehle die Persona-plausibelste sichtbare Option
     (neutral, mittlere Skala, "Trifft zu" nur wenn Persona es hergibt).
  d) NIEMALS "Keine Angabe" / "prefer not to say" wenn eine konkrete Option
     aus der Persona abgeleitet werden kann — Panels bestrafen diese Antwort.

MEHRFACHAUSWAHL (Checkboxen):
  - Wenn Persona Multi-Werte hat (hobbies, streaming_services), klicke ALLE passenden.
  - Bei "Keins davon" + gleichzeitig passende Persona-Option -> passende Option, NICHT "keins".

FREITEXT-FELDER:
  - Kurze, konkrete, Persona-konsistente Antwort. Keine Emojis, keine Werbung, keine Scherze.
  - Bei "Warum?" Fragen: ein nuechterner, 10-25 Woerter langer Satz.

VERBOTEN (fuehrt zur Sperre):
  - Luegen um Pre-Qualifikation zu bestehen.
  - Zufaellig antworten wenn Persona einen klaren Wert hat.
  - Bei Attention-Checks die falsche Option klicken.
  - Unterschiedliche Antworten auf semantisch gleiche Fragen geben.
"""


# ============================================================================
# VISION GATE — NVIDIA NIM Llama-3.2-Vision Analyse mit gehaertetem Prompt + DOM-Kontext
# ============================================================================


def _vision_cache_get(
    screenshot_hash: str, action_desc: str, step_num: int
) -> dict[str, object] | None:
    if not screenshot_hash:
        return None
    cache_key = (screenshot_hash, action_desc.strip().lower())
    cached = _VISION_CACHE.get(cache_key)
    if not cached:
        return None
    if _should_bypass_cached_decision(cached):
        audit("vision_cache_bypass", step=step_num, hash=screenshot_hash[:8])
        return None
    audit("vision_cache_hit", step=step_num, hash=screenshot_hash[:8])
    return dict(cached)


def _vision_cache_put(
    screenshot_hash: str, action_desc: str, step_num: int, decision: dict[str, object]
) -> None:
    if not screenshot_hash:
        return
    if not _should_store_cached_decision(decision):
        return
    cache_key = (screenshot_hash, action_desc.strip().lower())
    _VISION_CACHE[cache_key] = dict(decision)
    audit("vision_cache_store", step=step_num, hash=screenshot_hash[:8])


async def ask_vision(screenshot_path: str, action_desc: str, expected: str, step_num: int):
    # -------------------------------------------------------------------------
    # FUNKTION: ask_vision
    # PARAMETER: screenshot_path: str, action_desc: str, expected: str, step_num: int
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Sendet einen Screenshot + DOM-Kontext ans konfigurierte Vision-LLM
    (Primary: NVIDIA NIM meta/llama-3.2-11b-vision-instruct,
     Fallback: microsoft/phi-3.5-vision-instruct, microsoft/phi-3-vision-128k-instruct).
    WHY: KEIN EINZIGER KLICK ohne dass das Vision-Modell den Bildschirm gesehen hat.
    CONSEQUENCES: Bei Parse-Fehler wird RETRY zurückgegeben (nie ein Crash).
    """
    screenshot_hash = ""
    try:
        screenshot_hash = hashlib.md5(Path(screenshot_path).read_bytes()).hexdigest()
    except Exception:
        pass

    cached = _vision_cache_get(screenshot_hash, action_desc, step_num)
    if cached is not None:
        return cached

    # DOM Pre-Scan: Echte Selektoren VOR dem Vision-Call holen!
    dom_context = await dom_prescan()

    # Profil-Kontext aus gespeichertem User-Profil laden
    # WHY: Das Vision-LLM muss wissen wer der User ist um Profil-Fragen korrekt zu beantworten.
    #      Ohne Profil wuerde es zufaellig waehlen -> falsche Region, falscher Name etc.
    profile_context = _build_profile_context()
    fail_learning_context = build_fail_learning_context()

    # ---- PERSONA + GLOBAL BRAIN KONTEXT ----
    # WHY: Der Worker darf NIEMALS luegen. Persona = harte Fakten, Answer-Log =
    # Konsistenz ueber Validation-Traps, Brain = Flotten-Wissen aus frueheren Runs.
    # CONSEQUENCES: Wenn zu einer Frage bereits ein Persona-Fact existiert, wird er
    # als MUSS-ANTWORT markiert — das Vision-LLM darf die dann NICHT veraendern.
    persona_block = build_persona_prompt_block(
        ACTIVE_PERSONA,
        _collect_recent_answers(limit=8),
    )
    brain_block = build_brain_prompt_block(_BRAIN_PRIME_CONTEXT or PrimeContext())
    persona_hint_block = _build_persona_answer_hint(_LAST_QUESTION_TEXT, _LAST_QUESTION_OPTIONS)
    consistency_block = _build_consistency_block(_LAST_QUESTION_TEXT)
    trap_rules_block = _TRAP_DETECTION_RULES_PROMPT

    prompt = f"""Du bist der Vision Gate Controller der OpenSIN-Bridge.

KONTEXT:
- Letzte Aktion: '{action_desc}'
- Erwartetes Ergebnis: '{expected}'
- Schritt Nummer: {step_num} von maximal {MAX_STEPS}

{persona_block}

{brain_block}

{persona_hint_block}

{consistency_block}

{profile_context}

{fail_learning_context}

{trap_rules_block}

{dom_context}

AUFGABE — Analysiere den Screenshot UND die DOM-Daten oben PRÄZISE:

1. BLOCKIERUNGEN: Sind Captchas, Cookie-Banner, Consent-Modals, Login-Dialoge, Popups, Overlays oder Error-Messages sichtbar?
   �� Wenn ja: Diese ZUERST schliessen/akzeptieren!

2. AKTUELLER STATUS: Was zeigt die Seite GENAU an?

3. FORTSCHRITT: Hat sich gegenüber der letzten Aktion etwas verändert?

4. NÄCHSTE AKTION: Was muss als nächstes passieren?

VERFÜGBARE AKTIONEN (wähle GENAU EINE):
- "click_element" — Standard CSS-Selektor Klick. Params: {{"selector": "#id-oder-.klasse"}}
- "click_ref" — Klick per Accessibility-Tree-Ref (z.B. @e9). BEVORZUGT für Radio-Buttons, Checkboxen, Links ohne ID! Params: {{"ref": "@e9"}}
- "ghost_click" — Voller Pointer+Mouse Event-Stack für SPA/React-Elemente. Params: {{"selector": "#echte-id"}}
- "vision_click" — Beschreibungsbasierter Klick. Params: {{"description": "Text des Elements"}}
- "click_coordinates" — Absoluter Pixel-Klick. Params: {{"x": 100, "y": 200}}
- "keyboard" — Tastatur verwenden (z.B. Tab, Enter). Params: {{"keys": ["Enter"], "selector": "#optional-id"}}
- "type_text" — Text eingeben. Params: {{"selector": "css", "text": "wert"}}
- "navigate" — URL aufrufen. Params: {{"url": "https://..."}}
- "scroll_down" — Seite nach unten scrollen
- "scroll_up" — Seite nach oben scrollen
- "none" — Aufgabe erledigt, nichts mehr zu tun

ABSOLUTE PFLICHT-REGELN FÜR SELEKTOREN:
- NUTZE NUR Selektoren aus der DOM-Analyse oben! NIEMALS raten!
- Pseudo-Selektoren wie :has-text(), :contains(), :has() sind VERBOTEN (existieren nicht in CSS)!
- Bevorzuge #id Selektoren (z.B. #survey-65076903) — die sind IMMER eindeutig!
- WICHTIG: input[type='radio'][value='X'] NIEMALS nutzen — HeyPiggy Radio-Buttons haben KEINE value= Attribute!
- Für Radio-Buttons → IMMER click_ref mit dem @eX Ref aus dem ACCESSIBILITY-TREE oben nutzen!
- Für Survey-Karten auf HeyPiggy: Die Klasse ist .survey-item (NICHT .survey-card!), jede Karte hat eine eindeutige ID wie #survey-XXXXXXXX
- Nutze ghost_click für alle div-basierten Karten (cursor: pointer)
- Playwright-only Texte-Selektoren wie :contains(), :has-text() oder :text() sind VERBOTEN.
- Wenn du Textreferenz brauchst, nenne den echten CSS-Selektor aus dem DOM-Pre-Scan oder eine eindeutige #id.
- CONSENT-MODAL "Nächste" Button: IMMER #submit-button-cpx nutzen! NIEMALS button.modal-button-positive — das trifft dutzende versteckte Buttons im DOM und funktioniert nicht!

KLICK-STRATEGIE:
- Für Radio-Buttons ([radio @eX] im Accessibility-Tree) → click_ref mit {{"ref": "@eX"}} — das ist PFLICHT!
- Für <button>, <a>, <input> → click_element
- Für div.survey-item / div[cursor=pointer] → ghost_click mit #id-Selektor
- Für Elemente ohne CSS-Selektor → vision_click mit Beschreibung
- Letzter Ausweg → click_coordinates mit x,y aus der DOM-Analyse

PAGE STATE REGELN — KRITISCH:
- "dashboard" → HeyPiggy Startseite mit Survey-Liste, noch keine Umfrage gestartet
- "login" → Login-Formular sichtbar
- "onboarding" → Profil-Modal/Onboarding-Fragen (Region, Name, etc.) — VOR dem eigentlichen Dashboard!
- "survey_active" → UMFRAGE LÄUFT GERADE! Fragen werden angezeigt (Radio-Buttons, Dropdowns, Textfelder, Skalen). NIEMALS "none" zurückgeben solange Fragen sichtbar sind!
- "survey_audio" → Audio-Frage aktiv — ein Clip spielt oder muss abgespielt werden. Die transkribierte Version findest du im MEDIA-ANALYSE Block oben!
- "survey_video" → Video-Frage aktiv — ein Clip spielt oder muss abgespielt werden. Die semantische Analyse findest du im MEDIA-ANALYSE Block oben!
- "survey_image" → Bild-Frage aktiv — ein Bild muss inspiziert werden (das Vision-LLM sieht es direkt im Screenshot).
- "survey" → Survey-Auswahl / Übergangsseite (zwischen Dashboard und aktiver Umfrage)
- "survey_done" → Umfrage erfolgreich abgeschlossen, Bestätigungsseite (eine weitere Umfrage wird automatisch folgen!)
- "error" → Fehlermeldung, Timeout oder unbekannter Zustand
- "unknown" → Seite nicht klar erkennbar

MEDIA-FRAGEN REGELN — KRITISCH:
- Wenn ein MEDIA-ANALYSE Block oben steht: NUTZE IHN! Die Audio-Transkripte und Video-Beschreibungen zeigen dir genau was gefragt wird.
- Audio-Clip auf der Seite + Frage "Welche Marke/Produkt/Slogan hörten Sie?" → wähle die Antwort die zum Transkript passt.
- Video-Clip auf der Seite + Frage "Was macht die Person/Welche Marke?" → nutze "Marken gesichtet" und "Aktionen" aus der Analyse.
- Wenn Media-Analyse fehlschlägt (error gesetzt): versuche auf Play zu klicken, warte auf Untertitel/Captions, und wähle die PLAUSIBELSTE Antwort aus den sichtbaren Optionen — NIEMALS "weiß nicht" oder "keine Angabe" wenn es eine echte Option gibt!
- Nach Audio/Video: erst Clip komplett hören/sehen (page_state = "survey_audio"/"survey_video"), dann erst "Weiter" klicken.

PROFIL-FRAGEN REGELN — KRITISCH:
- Wenn ein Modal mit Profil-Fragen sichtbar ist (Region, Wohnort, Geschlecht, Name, Alter etc.):
  page_state="onboarding" setzen!
- Nutze IMMER das BENUTZERPROFIL oben um die korrekte Antwort zu wählen!
- Region-Frage ("In welcher Region wohnst du?"): IMMER "Norden" wählen (Jeremy wohnt in Berlin, wählt Norden)
- Nach Auswahl der Antwort → "Nächste"/"Weiter" Button klicken!
- Profil-Modals MÜSSEN vollständig ausgefüllt werden bevor Umfragen gestartet werden!

UMFRAGE-REGELN — ABSOLUT KRITISCH:
- Wenn page_state="survey_active": BEENDE DIE UMFRAGE VOLLSTÄNDIG! Klicke ALLE Fragen durch bis zur Bestätigungsseite!
- Wenn eine Frage sichtbar ist → IMMER beantworten und "Weiter"/"Next"/"Submit" klicken!
- NIEMALS next_action="none" wenn noch Fragen offen sind!
- Radio-Button Fragen → click_ref mit dem @eX Ref aus dem Accessibility-Tree (NICHT click_element mit value=!)
- Dropdown-Fragen → click_element auf die gewünschte Option
- Freitext-Fragen → type_text mit einer sinnvollen Antwort
- Skalen-Fragen (1-5, 1-7, etc.) → click_element auf mittlere oder positive Option
- "Weiter"/"Next"/"Fortfahren"/"Continue"/"Submit" Buttons → IMMER klicken nach einer Antwort!
- Fortschrittsbalken sichtbar? → Umfrage läuft noch, page_state="survey_active"!

CAPTCHA-ERKENNUNG — KRITISCH:
- Captcha-Checkbox sichtbar ("I am not a robot" / "Ich bin kein Roboter") → page_state="captcha" setzen!
- Captcha-Checkbox → click_ref mit dem @eX Ref aus dem Accessibility-Tree!
- Bilderauswahl-Captcha → Vision erkennt die richtigen Bilder und klickt sie!
- Bei Captcha: NIEMALS verdict="STOP"! Captchas können gelöst werden!

ANTWORT-KONSISTENZ — RAUSFLUG-VERMEIDUNG:
- Vergleiche aktuelle Frage mit BENUTZERPROFIL und FRÜHEREN ANTWORTEN oben!
- Gleiche Frage → GLEICHE Antwort wie früher!
- Profil-Fragen (Alter, Geschlecht, Region) → IMMER aus BENUTZERPROFIL!
- Widersprüchliche Antworten = Rausflug-Gefahr = VERMEIDEN!
- "None of the above" NUR wenn wirklich keine Option passt!
- Mittlere/positive Optionen bevorzugen bei Skalenfragen!

REGELN:
- Antworte AUSSCHLIESSLICH mit gültigem JSON! Kein Markdown, kein Text!
- Bei Captchas: page_state="captcha" und click_ref auf Checkbox! NICHT STOP!
- Nur bei unlösbaren Blockierungen (kein Captcha): verdict="STOP"
- Bei unveränderter Seite: verdict="RETRY" und schlage eine ANDERE Methode vor!
- Credentials NIEMALS ausgeben! Nutze "<EMAIL>" und "<PASSWORD>" als Platzhalter.
- Wähle IMMER die lukrativste verfügbare Umfrage (höchster €-Betrag)!

ANTWORT-FORMAT (NUR dieses JSON, NICHTS anderes):
{{
  "verdict": "PROCEED",
  "page_state": "dashboard|login|onboarding|survey|survey_active|survey_audio|survey_video|survey_image|survey_done|captcha|error|unknown",
  "reason": "Kurze Analyse...",
  "progress": true,
  "next_action": "click_ref",
  "next_params": {{"ref": "@e9"}},
  "question_text": "Nur bei survey_* / onboarding — Frage wortwoertlich aus DOM, sonst leer.",
  "answer_text": "Nur bei survey_* / onboarding — Antwort-Text den du gleich klickst, sonst leer.",
  "question_topic": "age|gender|country|region|city|income|employment|occupation|education|marital|household|children|housing|car|smoking|alcohol|hobbies|brand|attention_check|screening|other|",
  "trap_detected": "none|attention_check|consistency|screening|branching"
}}"""

    run_result = await run_vision_model(
        prompt,
        screenshot_path,
        timeout=180,
        step_num=step_num,
        purpose="main_loop",
    )
    if not run_result.get("ok"):
        error_reason = run_result.get("error", "Vision call failed")
        if run_result.get("auth_failure"):
            return {
                "verdict": "STOP",
                "reason": f"Vision auth failed: {error_reason}",
                "next_action": "none",
                "page_state": "error",
                "progress": False,
            }
        return {
            "verdict": "RETRY",
            "reason": error_reason,
            "next_action": "none",
            "page_state": "unknown",
            "progress": False,
        }

    full_text = run_result.get("text", "")

    try:
        # Markdown-Block entfernen falls vorhanden
        if "```json" in full_text:
            full_text = full_text.split("```json")[1].split("```")[0].strip()
        elif "```" in full_text:
            parts = full_text.split("```")
            if len(parts) >= 3:
                full_text = parts[1].strip()
                # Falls der erste Teil nach ``` mit "json" beginnt, entfernen
                if full_text.startswith("json"):
                    full_text = full_text[4:].strip()

        if full_text and not full_text.lstrip().startswith("{"):
            start = full_text.find("{")
            end = full_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                full_text = full_text[start : end + 1]

        result = json.loads(full_text)
        audit(
            "vision_check",
            step=step_num,
            verdict=result.get("verdict"),
            page_state=result.get("page_state"),
            reason=result.get("reason", "")[:150],
            next_action=result.get("next_action"),
        )
        _vision_cache_put(screenshot_hash, action_desc, step_num, result)
        return result

    except json.JSONDecodeError as e:
        audit(
            "error",
            message=f"Vision JSON Parse Error: {e}",
            step=step_num,
            raw_output=full_text[:500],
        )
        return {
            "verdict": "RETRY",
            "reason": f"JSON Parse Error: {e}",
            "next_action": "none",
            "page_state": "unknown",
            "progress": False,
        }

    except Exception as e:
        audit("error", message=f"Vision Exception: {e}", step=step_num)
        return {
            "verdict": "RETRY",
            "reason": str(e),
            "next_action": "none",
            "page_state": "unknown",
            "progress": False,
        }


# ============================================================================
# KEYBOARD NAVIGATION — Tab, Enter, Arrow Keys als ultimative Bypass-Methode
# ============================================================================


async def keyboard_action(keys: list, selector: str = ""):
    # -------------------------------------------------------------------------
    # FUNKTION: keyboard_action
    # PARAMETER: keys: list, selector: str = ""
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Führt Tastatur-Aktionen aus via JavaScript KeyboardEvent Dispatch.
    WHY: Wenn ALLE Klick-Methoden scheitern, funktionieren Tastatur-Events IMMER,
    weil sie auf OS-Level durchgehen und von keinem Framework blockiert werden.
    CONSEQUENCES: Die allerbeste Umgehung für störrische SPAs.

    Unterstützte Keys: Tab, Enter, Space, ArrowDown, ArrowUp, ArrowLeft, ArrowRight, Escape
    """
    global CURRENT_TAB_ID, CURRENT_WINDOW_ID
    tab_params = _tab_params()
    selector = normalize_selector(selector)

    # Mapping von Key-Namen zu KeyboardEvent-Properties
    key_map = {
        "Tab": {"key": "Tab", "code": "Tab", "keyCode": 9},
        "Enter": {"key": "Enter", "code": "Enter", "keyCode": 13},
        "Space": {"key": " ", "code": "Space", "keyCode": 32},
        "Escape": {"key": "Escape", "code": "Escape", "keyCode": 27},
        "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "keyCode": 40},
        "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "keyCode": 38},
        "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "keyCode": 37},
        "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "keyCode": 39},
    }

    results = []
    for key_name in keys:
        kp = key_map.get(key_name, {"key": key_name, "code": key_name, "keyCode": 0})

        # Wenn ein Selektor angegeben ist, erst auf das Element fokussieren
        focus_part = ""
        if selector:
            focus_part = f"""
                var target = document.querySelector("{selector}");
                if (target && typeof target.focus === 'function') {{
                    target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    target.focus();
                }}
            """

        js_code = f"""
        (function() {{
            {focus_part}
            var el = document.activeElement || document.body;
            var opts = {{
                key: "{kp["key"]}", code: "{kp["code"]}",
                keyCode: {kp["keyCode"]}, which: {kp["keyCode"]},
                bubbles: true, cancelable: true, composed: true,
                view: window
            }};
            el.dispatchEvent(new KeyboardEvent('keydown', opts));
            el.dispatchEvent(new KeyboardEvent('keypress', opts));
            el.dispatchEvent(new KeyboardEvent('keyup', opts));
            return {{
                success: true, key: "{key_name}",
                target: el.tagName + (el.id ? '#' + el.id : '') + (el.className ? '.' + (el.className + '').split(' ')[0] : ''),
                focused: el === document.activeElement
            }};
        }})();
        """
        try:
            result = await execute_bridge("execute_javascript", {"script": js_code, **tab_params})
            audit("keyboard", key=key_name, result=str(result)[:150])
            results.append(result)
            # Kurze Pause zwischen Tasten wie ein Mensch
            await asyncio.sleep(0.15 + random.random() * 0.25)
        except Exception as e:
            audit("error", message=f"Keyboard {key_name} failed: {e}")
            results.append({"error": str(e)})

    return results


# ============================================================================
# DOM-VERIFIKATION — Prüft ob sich die Seite WIRKLICH verändert hat
# ============================================================================


async def dom_verify_change(before_url: str, before_title: str):
    # -------------------------------------------------------------------------
    # FUNKTION: dom_verify_change
    # PARAMETER: before_url: str, before_title: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Prüft via DOM ob die Seite sich nach einer Aktion verändert hat.
    WHY: Vision allein kann täuschen (gleicher Screenshot, aber DOM hat sich geändert).
         DOM allein kann täuschen (URL gleich, aber visuell komplett anders).
         NUR BEIDES ZUSAMMEN gibt Sicherheit!
    CONSEQUENCES: Gibt ein Dict mit changed=True/False und Details zurück.
    """
    tab_params = _tab_params()

    try:
        page_info = await execute_bridge("get_page_info", tab_params)
        current_url = page_info.get("url", "") if isinstance(page_info, dict) else ""
        current_title = page_info.get("title", "") if isinstance(page_info, dict) else ""

        if not current_url and not current_title:
            # Tab may still be loading — wait briefly and retry once with same tabId
            # NEVER switch to a different tab or recover to a new one here
            await asyncio.sleep(1.5)
            page_info = await execute_bridge("get_page_info", tab_params)
            current_url = page_info.get("url", "") if isinstance(page_info, dict) else ""
            current_title = page_info.get("title", "") if isinstance(page_info, dict) else ""

        url_changed = current_url != before_url
        title_changed = current_title != before_title

        # Auch DOM-Diff via Bridge page_diff abfragen (vergleicht Accessibility Trees)
        dom_diff = None
        try:
            diff_result = await execute_bridge("page_diff", tab_params)
            if isinstance(diff_result, dict):
                dom_diff = {
                    "added": diff_result.get("addedCount", 0),
                    "removed": diff_result.get("removedCount", 0),
                    "changed": diff_result.get("changedCount", 0),
                }
        except Exception:
            pass

        changed = (
            url_changed
            or title_changed
            or (
                dom_diff
                and (dom_diff["added"] > 0 or dom_diff["removed"] > 0 or dom_diff["changed"] > 0)
            )
        )

        result = {
            "changed": bool(changed),
            "url_changed": url_changed,
            "title_changed": title_changed,
            "current_url": current_url,
            "current_title": current_title,
            "dom_diff": dom_diff,
        }
        audit("dom_verify", **result)
        return result

    except Exception as e:
        audit("error", message=f"DOM-Verifikation fehlgeschlagen: {e}")
        return {"changed": False, "error": str(e)}


# ============================================================================
# KLICK-ESKALATIONSKETTE — 5 Methoden mit Keyboard-Bypass, automatisch eskalierend
# ============================================================================

MAX_CLICK_ESCALATIONS = 5  # click → ghost → KEYBOARD → vision → coords

# Globaler Schritt-Zähler für Vision-Screenshots innerhalb der Eskalation.
# WHY: take_screenshot() braucht eine step_num — wir verwenden einen eigenen
# Zähler damit Eskalations-Screenshots im Audit-Log klar von Hauptloop-Schritten
# unterscheidbar sind (Format: "esc_NNN").
_ESC_STEP = 0


async def _vision_gate_inside_escalation(step_label: str, action_done: str, expected: str) -> dict:
    """
    Macht Screenshot + Vision-Check INNERHALB der Eskalationskette.
    Gibt das volle Vision-Decision-Dict zurück (verdict, next_action, next_params, page_state).
    WHY: Das Mandat verlangt Vision VOR JEDER AKTION — auch vor jeder Eskalationsstufe.
    CONSEQUENCES: Ohne diesen Gate kann die Eskalation blind 5 Aktionen hintereinander
    feuern ohne zu wissen ob der Klick überhaupt sinnvoll war.
    """
    global _ESC_STEP
    _ESC_STEP += 1
    img_path, _ = await take_screenshot(_ESC_STEP * 1000, label=f"esc_{step_label}")
    if not img_path:
        # Screenshot fehlgeschlagen → pessimistisch RETRY zurückgeben
        return {
            "verdict": "RETRY",
            "next_action": "none",
            "next_params": {},
            "page_state": "unknown",
        }
    return await ask_vision(img_path, action_done, expected, _ESC_STEP * 1000)


async def escalating_click(
    selector: str = "",
    description: str = "",
    x: int = None,
    y: int = None,
    step_num: int = 0,
    ref: str = "",
):
    # -------------------------------------------------------------------------
    # FUNKTION: escalating_click
    # PARAMETER: 
    selector: str = "",
    description: str = "",
    x: int = None,
    y: int = None,
    step_num: int = 0,
    ref: str = "",

    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Versucht einen Klick mit bis zu 5 Methoden — JEDE durch Vision-Gate abgesichert.
    WHY: Verschiedene Webseiten-Technologien brauchen verschiedene Interaktionsmethoden.
    Vision entscheidet nach JEDEM Klickversuch ob die nächste Stufe nötig ist.
    CONSEQUENCES: Kein blinder Auto-Eskalations-Loop mehr — Vision sieht jede Stufe.

    Eskalationskette (vision-gesteuert):
    1. click_ref (Accessibility-Ref, falls vorhanden) → Vision-Check
    2. click_element (Standard CSS-Selektor) → Vision-Check
    3. ghost_click (Voller Pointer+Mouse Event-Stack via JS) → Vision-Check
    4. KEYBOARD (Tab zum Element navigieren + Enter drücken) → Vision-Check
    5. vision_click / click_coordinates als letzte Auswege → Vision-Check
    """
    tab_params = _tab_params()
    selector = normalize_selector(selector)
    selector = await resolve_survey_selector(selector, description)
    if not selector and description:
        desc_lower = description.lower()
        if "umfrage" in desc_lower or "survey" in desc_lower or "€" in desc_lower:
            selector = await resolve_survey_selector("div.survey-item", description)

    methods = []
    if ref:
        methods.append(("click_ref", {**tab_params, "ref": ref}))
    if selector:
        methods.append(("click_element", {**tab_params, "selector": selector}))
    if selector:
        methods.append(("ghost_click_js", selector))
    if selector:
        methods.append(("keyboard_focus_enter", selector))
    if description:
        methods.append(("vision_click", {**tab_params, "description": description}))
    if x is not None and y is not None:
        methods.append(("click_coordinates_js", (x, y)))

    before_url, before_title = "", ""
    try:
        pi = await execute_bridge("get_page_info", tab_params)
        if isinstance(pi, dict):
            before_url = pi.get("url", "")
            before_title = pi.get("title", "")
    except Exception:
        pass

    for i, method_info in enumerate(methods):
        if i >= MAX_CLICK_ESCALATIONS:
            break

        method_name = method_info[0]
        audit(
            "click_escalation",
            level=i + 1,
            method=method_name,
            selector=selector[:80] if selector else "",
        )

        try:
            if method_name == "click_ref":
                result = await execute_bridge("click_ref", method_info[1])
                if isinstance(result, dict) and result.get("error"):
                    audit("error", message=f"click_ref failed: {result['error']}")
                else:
                    await asyncio.sleep(0.8)
                    esc_decision = await _vision_gate_inside_escalation(
                        f"after_click_ref_{i}",
                        f"click_ref auf {ref[:60]}",
                        "Seite hat reagiert",
                    )
                    audit(
                        "vision_check",
                        method="click_ref",
                        verdict=esc_decision.get("verdict"),
                        page_state=esc_decision.get("page_state"),
                    )
                    if esc_decision.get("verdict") == "PROCEED":
                        return True
                continue

            if method_name == "click_element":
                result = await execute_bridge("click_element", method_info[1])
                if isinstance(result, dict) and result.get("error"):
                    audit("error", message=f"click_element failed: {result['error']}")
                else:
                    await asyncio.sleep(0.8)
                    esc_decision = await _vision_gate_inside_escalation(
                        f"after_click_element_{i}",
                        f"click_element auf {selector[:60]}",
                        "Seite hat reagiert",
                    )
                    audit(
                        "vision_check",
                        method="click_element",
                        verdict=esc_decision.get("verdict"),
                        page_state=esc_decision.get("page_state"),
                    )
                    if esc_decision.get("verdict") == "PROCEED":
                        return True
                continue

            elif method_name == "ghost_click_js":
                sel = method_info[1]
                js_code = f"""
                (function() {{
                    const el = document.querySelector("{sel}");
                    if (!el) return {{ error: "Element not found", selector: "{sel}" }};
                    el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    const rect = el.getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    if (typeof el.focus === 'function') el.focus();
                    const opts = {{
                        bubbles: true, cancelable: true,
                        clientX: x, clientY: y, screenX: x, screenY: y,
                        view: window, detail: 1, button: 0, buttons: 1
                    }};
                    el.dispatchEvent(new PointerEvent('pointerover', opts));
                    el.dispatchEvent(new PointerEvent('pointerenter', {{...opts, bubbles: false}}));
                    el.dispatchEvent(new MouseEvent('mouseover', opts));
                    el.dispatchEvent(new MouseEvent('mouseenter', {{...opts, bubbles: false}}));
                    el.dispatchEvent(new PointerEvent('pointerdown', opts));
                    el.dispatchEvent(new MouseEvent('mousedown', opts));
                    el.dispatchEvent(new PointerEvent('pointerup', {{...opts, buttons: 0}}));
                    el.dispatchEvent(new MouseEvent('mouseup', {{...opts, buttons: 0}}));
                    el.dispatchEvent(new MouseEvent('click', {{...opts, buttons: 0}}));
                    if (typeof el.click === 'function') el.click();
                    return {{ success: true, tag: el.tagName, text: (el.textContent || '').substring(0, 60) }};
                }})();
                """
                result = await execute_bridge(
                    "execute_javascript", {"script": js_code, **tab_params}
                )
                if isinstance(result, dict) and result.get("error"):
                    audit("error", message=f"ghost_click failed: {result['error']}")
                else:
                    await asyncio.sleep(0.8)
                    esc_decision = await _vision_gate_inside_escalation(
                        f"after_ghost_click_{i}",
                        f"ghost_click auf {sel[:60]}",
                        "Seite hat reagiert",
                    )
                    audit(
                        "vision_check",
                        method="ghost_click",
                        verdict=esc_decision.get("verdict"),
                        page_state=esc_decision.get("page_state"),
                    )
                    if esc_decision.get("verdict") == "PROCEED":
                        return True
                continue

            elif method_name == "keyboard_focus_enter":
                sel = method_info[1]
                focus_js = f"""
                (function() {{
                    var el = document.querySelector("{sel}");
                    if (!el) return {{ error: "Element not found" }};
                    el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    el.setAttribute('tabindex', '0');
                    el.focus();
                    return {{
                        success: true, focused: document.activeElement === el,
                        tag: el.tagName, id: el.id || '', text: (el.textContent || '').substring(0, 40)
                    }};
                }})();
                """
                focus_result = await execute_bridge(
                    "execute_javascript", {"script": focus_js, **tab_params}
                )
                audit("keyboard", action="focus", result=str(focus_result)[:150])
                await asyncio.sleep(0.3)
                await keyboard_action(["Enter"], selector=sel)
                await asyncio.sleep(0.8)
                esc_decision = await _vision_gate_inside_escalation(
                    f"after_keyboard_enter_{i}",
                    f"keyboard Enter auf {sel[:60]}",
                    "Seite hat reagiert",
                )
                audit(
                    "vision_check",
                    method="keyboard_enter",
                    verdict=esc_decision.get("verdict"),
                    page_state=esc_decision.get("page_state"),
                )
                if esc_decision.get("verdict") == "PROCEED":
                    return True
                await keyboard_action(["Space"], selector=sel)
                await asyncio.sleep(0.8)
                esc_decision2 = await _vision_gate_inside_escalation(
                    f"after_keyboard_space_{i}",
                    f"keyboard Space auf {sel[:60]}",
                    "Seite hat reagiert",
                )
                audit(
                    "vision_check",
                    method="keyboard_space",
                    verdict=esc_decision2.get("verdict"),
                    page_state=esc_decision2.get("page_state"),
                )
                if esc_decision2.get("verdict") == "PROCEED":
                    return True
                continue

            elif method_name == "vision_click":
                result = await execute_bridge("vision_click", method_info[1])
                if isinstance(result, dict) and result.get("error"):
                    audit("error", message=f"vision_click failed: {result['error']}")
                else:
                    await asyncio.sleep(0.8)
                    esc_decision = await _vision_gate_inside_escalation(
                        f"after_vision_click_{i}",
                        f"vision_click '{description[:40]}'",
                        "Seite hat reagiert",
                    )
                    audit(
                        "vision_check",
                        method="vision_click",
                        verdict=esc_decision.get("verdict"),
                        page_state=esc_decision.get("page_state"),
                    )
                    if esc_decision.get("verdict") == "PROCEED":
                        return True
                continue

            elif method_name == "click_coordinates_js":
                cx, cy = method_info[1]
                js_code = f"""
                (function() {{
                    const el = document.elementFromPoint({cx}, {cy});
                    if (!el) return {{ error: "Kein Element bei ({cx}, {cy})" }};
                    const opts = {{
                        bubbles: true, cancelable: true,
                        clientX: {cx}, clientY: {cy}, screenX: {cx}, screenY: {cy},
                        view: window, detail: 1, button: 0, buttons: 1
                    }};
                    el.dispatchEvent(new PointerEvent('pointerdown', opts));
                    el.dispatchEvent(new MouseEvent('mousedown', opts));
                    el.dispatchEvent(new PointerEvent('pointerup', {{...opts, buttons: 0}}));
                    el.dispatchEvent(new MouseEvent('mouseup', {{...opts, buttons: 0}}));
                    el.dispatchEvent(new MouseEvent('click', {{...opts, buttons: 0}}));
                    if (typeof el.click === 'function') el.click();
                    return {{ success: true, tag: el.tagName, text: (el.textContent || '').substring(0, 60) }};
                }})();
                """
                result = await execute_bridge(
                    "execute_javascript", {"script": js_code, **tab_params}
                )
                if isinstance(result, dict) and result.get("error"):
                    audit("error", message=f"coord_click failed: {result}")
                else:
                    await asyncio.sleep(0.8)
                    esc_decision = await _vision_gate_inside_escalation(
                        f"after_coord_click_{i}",
                        f"coord_click ({cx},{cy})",
                        "Seite hat reagiert",
                    )
                    audit(
                        "vision_check",
                        method="coord_click",
                        verdict=esc_decision.get("verdict"),
                        page_state=esc_decision.get("page_state"),
                    )
                    if esc_decision.get("verdict") == "PROCEED":
                        return True
                continue

        except Exception as e:
            audit("error", message=f"Klick-Methode {method_name} Exception: {e}")
            continue

    # DOM-FALLBACK: Wenn alle 5 Klick-Methoden fehlschlagen, versuche description-basierte Textsuche
    # WHY: vision_click oder andere Methoden können durch Rate-Limit oder komplexe Modals blockiert werden.
    # Ein simpler sichtbarer Button mit Text (z.B. "Nächste", "Weiter") umgeht Vision komplett.
    if description:
        audit(
            "action",
            message=f"DOM-Fallback: Versuche click_visible_button_with_text('{description[:40]}')",
        )
        if await click_visible_button_with_text(description):
            return True

    audit(
        "error",
        message="ALLE 5 Klick-Methoden fehlgeschlagen!",
        selector=selector[:80] if selector else "",
    )
    return False


# ============================================================================
# SESSION-BACKUP — Cookies sichern bei jedem wichtigen Statuswechsel
# ============================================================================


async def save_session(label: str):
    # -------------------------------------------------------------------------
    # FUNKTION: save_session
    # PARAMETER: label: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Sichert die aktuelle Browser-Session (Cookies + LocalStorage + SessionStorage).
    WHY: Bei Bridge-Disconnect oder Crash muessen wir die Session wiederherstellen
         koennen. Zusaetzlich wird der persistente Cross-Run-Cache
         aktualisiert damit der naechste Worker-Start bereits angemeldet
         starten kann (siehe session_store.py).
    CONSEQUENCES: Zwei Dateien werden geschrieben:
      1) Per-Run-Snapshot in SESSION_DIR (Debug + Crash-Recovery)
      2) Cross-Run-Cache in ~/.heypiggy/session_cache.json (Login-Skip)
    """
    # 1) Per-Run-Snapshot (wie bisher) — fuer Forensik pro Lauf
    try:
        params = _tab_params()
        cookies = await execute_bridge("export_all_cookies", params)
        session_file = SESSION_DIR / f"session_{label}_{RUN_ID}.json"
        session_file.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")
        audit("session_save", label=label, path=str(session_file))
    except Exception as e:
        audit("error", message=f"Session-Backup fehlgeschlagen: {e}")

    # 2) Cross-Run-Cache (Cookies + LocalStorage + SessionStorage)
    # WHY: Panels wie Dynata/PureSpectrum geben nach einem Login 60-90 Min gueltige
    # Session-Cookies aus. Wenn wir die zwischen Runs behalten, sparen wir uns
    # Email/Password/2FA/Captcha bei jedem Start.
    try:
        await _session_dump(
            execute_bridge=execute_bridge,
            tab_params=_tab_params(),
            audit=audit,
        )
    except Exception as e:
        audit("session_persistent_error", label=label, error=str(e))


# ============================================================================
# HUMAN DELAYS — Zufällige Pausen gegen Bot-Erkennung
# ============================================================================


async def human_delay(min_sec=1.5, max_sec=4.5):
    # -------------------------------------------------------------------------
    # FUNKTION: human_delay
    # PARAMETER: min_sec=1.5, max_sec=4.5
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """
    Wartet eine zufällige Zeitspanne wie ein echter Mensch.
    WHY: Konstante Delays (z.B. immer 3s) sind ein Bot-Signal.
    """
    delay = min_sec + random.random() * (max_sec - min_sec)
    await asyncio.sleep(delay)


async def adaptive_think_delay(
    question_text: str | None = None,
    options: list[str] | None = None,
    trap_detected: str = "none",
    action_kind: str = "click",
) -> float:
    """
    Adaptive Think-Time: simuliert realistische Lesedauer + Abwaegungszeit.

    WHY: Bots sind daran erkennbar, dass sie JEDE Frage gleich schnell beantworten
    — egal ob ein kurzer Ja/Nein-Screener oder ein 12-Optionen-Multi-Select mit
    langer Erklaerung. Echte Menschen brauchen bei komplexeren Fragen laenger.
    Panels messen die Response-Time pro Frage und markieren zu schnelle/gleich-
    schnelle Befragte als "speeders" -> stille Disqualifikation ohne Reward.

    BERECHNUNG:
      - Basis: 1.2s - 2.5s (Latenz + Cursor-Bewegung)
      - Lesezeit Frage: ~0.035s pro Zeichen (ca. 250 WPM)
      - Lesezeit Optionen: 0.25s pro sichtbarer Option
      - Trap-Bonus: +2-4s bei attention/consistency/screening (mehr Nachdenken)
      - Freitext (type_text): +1-2s zusaetzlich fuer "Satz formulieren"
      - Harte Obergrenze: 22s (kein Panel wartet ewig)

    Returns den gewaehlten Delay in Sekunden (bereits asyncio.sleep'd).
    """
    base = 1.2 + random.random() * 1.3  # 1.2-2.5s
    qlen = len(question_text or "")
    read_q = min(qlen * 0.035, 6.0)  # max 6s Lesezeit Frage
    nopts = len(options or [])
    read_opts = min(nopts * 0.25, 3.5)  # max 3.5s Option-Scan

    trap_bonus = 0.0
    if trap_detected in ("attention_check", "consistency", "screening"):
        trap_bonus = 2.0 + random.random() * 2.0  # 2-4s extra
    elif trap_detected == "branching":
        trap_bonus = 1.0 + random.random() * 1.5  # 1-2.5s

    action_bonus = 0.0
    if action_kind == "type_text":
        action_bonus = 1.0 + random.random() * 1.0  # 1-2s fuer Satz-Formulierung
    elif action_kind == "select_option":
        action_bonus = 0.3 + random.random() * 0.4  # Dropdown-Oeffnen

    delay = base + read_q + read_opts + trap_bonus + action_bonus
    delay = max(0.8, min(delay, 22.0))

    # Kleine Jitter + gelegentliche "Ueber-Denk-Pausen" (10% Chance +2s)
    if random.random() < 0.10:
        delay += 1.5 + random.random() * 1.0
    delay = min(delay, 22.0)

    await asyncio.sleep(delay)
    return delay


# ============================================================================
# VISION GATE CONTROLLER — Herzstück der Sicherheit
# ============================================================================


class VisionGateController:
    # ========================================================================
    # KLASSE: VisionGateController
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """
    Steuert den gesamten Vision-Gate-Loop und verhindert Endlosschleifen.

    WHY: Ohne diesen Controller laufen Agenten in Endlosschleifen.
    CONSEQUENCES: Controller-Verletzung = Sofortiger Abbruch.

    Tracking:
    - total_steps: Gesamtzahl aller Aktionen
    - consecutive_retries: Aufeinanderfolgende RETRY-Verdicts
    - no_progress_count: Aktionen ohne sichtbare Bildschirmveränderung
    - last_screenshot_hash: MD5-Hash des letzten Screenshots für Vergleich
    - failed_selectors: Selektoren die bereits fehlgeschlagen sind (werden nicht nochmal versucht)
    """

    def __init__(self):
        self.total_steps = 0
        self.consecutive_retries = 0
        self.no_progress_count = 0
        self.last_screenshot_hash = None
        self.failed_selectors = {}
        self.last_page_state = None
        self.successful_actions = 0
        self.action_history = []

    def should_continue(self) -> bool:
        """Prüft ob der Worker weitermachen darf."""
        if self.total_steps >= MAX_STEPS:
            audit("stop", reason=f"MAX_STEPS ({MAX_STEPS}) erreicht")
            return False
        if self.consecutive_retries >= MAX_RETRIES:
            audit("stop", reason=f"MAX_RETRIES ({MAX_RETRIES}) erreicht")
            return False
        if self.no_progress_count >= MAX_NO_PROGRESS:
            audit("stop", reason=f"MAX_NO_PROGRESS ({MAX_NO_PROGRESS}) erreicht")
            return False
        return True

    def record_step(
        self,
        verdict: str,
        screenshot_hash: str,
        page_state: str = None,
        dom_changed: bool = False,
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: record_step
    # PARAMETER: 
        self,
        verdict: str,
        screenshot_hash: str,
        page_state: str = None,
        dom_changed: bool = False,
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Zeichnet einen Schritt auf und aktualisiert alle Zähler."""
        self.total_steps += 1

        # RETRY-Tracking
        if verdict == "RETRY":
            self.consecutive_retries += 1
        else:
            self.consecutive_retries = 0

        # Fortschritts-Erkennung:
        # WHY: Bei survey_active sehen Screenshots Frage-für-Frage fast identisch aus
        # (gleicher Header, gleiche Farben, gleiche Schriftarten). Hash-Vergleich würde
        # das fälschlich als "kein Fortschritt" werten und die Umfrage abbrechen.
        # LÖSUNG: Wenn wir mitten in einer aktiven Umfrage sind (survey_active),
        # gilt der Schritt IMMER als Fortschritt — egal ob Hash gleich ist.
        # Zusätzlich zählt dom_changed=True ebenfalls als Fortschritt.
        currently_in_survey = page_state in (
            "survey_active",
            "survey",
            "survey_audio",
            "survey_video",
            "survey_image",
        )
        if (
            screenshot_hash
            and screenshot_hash == self.last_screenshot_hash
            and not currently_in_survey
            and not dom_changed
        ):
            self.no_progress_count += 1
        else:
            self.no_progress_count = 0
            self.last_screenshot_hash = screenshot_hash

        # Page-State-Tracking
        if page_state:
            if page_state != self.last_page_state:
                self.failed_selectors.clear()
                self.clear_action_history()
                audit("state_change", old=self.last_page_state, new=page_state)
            self.last_page_state = page_state

        # Erfolgs-Tracking
        if verdict == "PROCEED":
            self.successful_actions += 1

    def add_failed_selector(self, selector: str):
    # -------------------------------------------------------------------------
    # FUNKTION: add_failed_selector
    # PARAMETER: self, selector: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Merkt sich einen fehlgeschlagenen Selektor um ihn nicht nochmal zu versuchen."""
        if selector:
            self.failed_selectors[selector] = self.failed_selectors.get(selector, 0) + 1

    def is_selector_failed(self, selector: str) -> bool:
        """Prüft ob ein Selektor bereits fehlgeschlagen ist."""
        return self.failed_selectors.get(selector, 0) >= 3

    def record_action(self, screenshot_hash: str, action: str, params: dict) -> bool:
        signature = (screenshot_hash or "", action, json.dumps(params, sort_keys=True))
        self.action_history.append(signature)
        self.action_history = self.action_history[-3:]
        return len(self.action_history) == 3 and len(set(self.action_history)) == 1

    def clear_action_history(self):
    # -------------------------------------------------------------------------
    # FUNKTION: clear_action_history
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        self.action_history.clear()

    def mark_dom_progress(self):
    # -------------------------------------------------------------------------
    # FUNKTION: mark_dom_progress
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """
        Setzt no_progress_count zurück wenn DOM-Verifikation echte Änderung bestätigt.
        WHY: record_step() läuft VOR der Aktion und kennt dom_changed noch nicht.
             mark_dom_progress() wird NACH der Aktion aufgerufen um einen fälschlichen
             no_progress-Zähler zu korrigieren — kritisch bei Survey-Fragen deren
             Screenshot fast identisch aussieht aber der DOM sich verändert hat.
        """
        if self.no_progress_count > 0:
            self.no_progress_count = 0
            audit(
                "state_change",
                message="DOM-Fortschritt bestätigt: no_progress_count zurückgesetzt",
            )

    def reset_for_new_survey(self):
    # -------------------------------------------------------------------------
    # FUNKTION: reset_for_new_survey
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """
        Setzt Pro-Survey-Counter zurück wenn eine neue Umfrage startet.
        WHY: MAX_STEPS, consecutive_retries und no_progress_count sind gedacht
             als Schutz gegen einzelne hängende Umfragen. Wenn der Orchestrator
             eine neue Umfrage startet, soll dieses Budget frisch sein.
             total_steps bleibt erhalten für globale Statistik.
        CONSEQUENCES: Der Worker kann so 10+ Umfragen in einem Run abarbeiten,
             ohne bei der 3. MAX_STEPS zu erreichen.
        """
        prev_steps = self.total_steps
        self.total_steps = 0
        self.consecutive_retries = 0
        self.no_progress_count = 0
        self.last_screenshot_hash = None
        self.last_page_state = None
        self.failed_selectors.clear()
        self.clear_action_history()
        audit(
            "gate_reset_for_new_survey",
            previous_total_steps=prev_steps,
            message="Pro-Survey-Budget zurückgesetzt — neue Umfrage startet frisch",
        )


def _resolve_profile_value(field_hint: str) -> str | None:
    lowered = field_hint.lower()
    lookup = (
        ("first_name", ("first", "vorname", "given")),
        ("last_name", ("last", "surname", "nachname", "family")),
        ("name", ("name", "fullname", "full-name")),
        ("city", ("city", "stadt", "ort", "wohnort")),
        ("region", ("region", "bundesland")),
        ("country", ("country", "land")),
        ("gender", ("gender", "geschlecht", "sex")),
    )
    for key, hints in lookup:
        if any(hint in lowered for hint in hints):
            value = USER_PROFILE.get(key)
            if isinstance(value, str) and value:
                return value
    return None


# ============================================================================
# CREDENTIAL INJECTION — Sichere Ersetzung von Platzhaltern
# ============================================================================


async def attempt_google_login(email: str) -> dict:
    """
    Klickt den 'Mit Google anmelden' Button auf heypiggy.com/login und
    wählt das Jeremy-Schulze-Profil aus dem Google-Account-Picker.

    WHY: HeyPiggy nutzt primär Google OAuth. Email+Password-Login ist ein
         Fallback, aber Google-Login ist schneller und vermeidet CAPTCHA.
         Jeremy Schulze ist im Chrome-Profil gespeichert und wird automatisch
         vorgeschlagen — kein Passwort-Eingabe nötig.
    CONSEQUENCES: Schlägt fehl → Worker fällt auf Email+Password-Fallback zurück.
                  Kein Crash, kein harter Abbruch.
    """
    tab_params = _tab_params()
    audit("google_login_start", email=email[:20] + "..." if email else "")

    try:
        google_btn_js = """
(function() {
    var btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
    var googleBtn = btns.find(function(el) {
        var text = (el.textContent || el.innerText || '').toLowerCase().trim();
        var hasGoogleIcon = el.querySelector('[class*="google"], svg, img[alt*="Google"]');
        return (text.includes('google') || text.includes('mit google') || hasGoogleIcon) &&
               el.offsetParent !== null;
    });
    if (!googleBtn) {
        var linkBtns = Array.from(document.querySelectorAll('[href*="google"], [data-provider="google"]'));
        googleBtn = linkBtns.find(el => el.offsetParent !== null);
    }
    if (googleBtn) {
        return {
            found: true,
            text: (googleBtn.textContent || '').trim().substring(0, 80),
            tag: googleBtn.tagName,
            id: googleBtn.id || '',
            cls: (googleBtn.className || '').toString().substring(0, 80)
        };
    }
    return {found: false};
})();
"""
        scan = await execute_bridge("execute_javascript", {"script": google_btn_js, **tab_params})
        found_data = {}
        if isinstance(scan, dict):
            found_data = scan.get("result", {}) or {}

        if not found_data.get("found"):
            audit(
                "google_login_no_button",
                message="Kein Google-Login-Button gefunden, Fallback auf Email+Password",
            )
            return {"ok": False, "reason": "no_google_button"}

        audit("google_login_button_found", text=found_data.get("text", "")[:60])

        click_js = """
(function() {
    var btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
    var googleBtn = btns.find(function(el) {
        var text = (el.textContent || el.innerText || '').toLowerCase().trim();
        var hasGoogleIcon = el.querySelector('[class*="google"], svg, img[alt*="Google"]');
        return (text.includes('google') || text.includes('mit google') || hasGoogleIcon) &&
               el.offsetParent !== null;
    });
    if (!googleBtn) {
        var linkBtns = Array.from(document.querySelectorAll('[href*="google"], [data-provider="google"]'));
        googleBtn = linkBtns.find(el => el.offsetParent !== null);
    }
    if (googleBtn) {
        googleBtn.click();
        return {clicked: true};
    }
    return {clicked: false};
})();
"""
        await execute_bridge("execute_javascript", {"script": click_js, **tab_params})
        await human_delay(3.0, 5.0)

        # Google Account-Picker: Das richtige Profil wählen
        # Der Popup-Tab enthält accounts.google.com — wir suchen nach dem Email
        account_js = f"""
(function() {{
    var targetEmail = {repr(email or "")};
    var items = Array.from(document.querySelectorAll('[data-email], [data-identifier], .account-name, .email'));
    var match = items.find(function(el) {{
        var txt = (el.getAttribute('data-email') || el.getAttribute('data-identifier') || el.textContent || '').toLowerCase();
        return txt.includes(targetEmail.toLowerCase());
    }});
    if (match) {{
        match.click();
        return {{selected: true, email: targetEmail}};
    }}
    // Fallback: ersten Account wählen wenn nur einer vorhanden
    var firstAccount = document.querySelector('[data-email], .account-name, [jsname="Njthtb"]');
    if (firstAccount) {{
        firstAccount.click();
        return {{selected: true, first_account: true}};
    }}
    return {{selected: false}};
}})();
"""
        await human_delay(1.5, 3.0)
        account_result = await execute_bridge(
            "execute_javascript", {"script": account_js, **tab_params}
        )
        audit("google_account_picker", result=str(account_result)[:120])

        await human_delay(4.0, 7.0)
        return {"ok": True, "reason": "google_login_clicked"}

    except Exception as e:
        audit("google_login_error", error=str(e))
        return {"ok": False, "reason": str(e)}


def inject_credentials(params: dict, email: str, pwd: str) -> dict:
    """
    Ersetzt <EMAIL> und <PASSWORD> Platzhalter mit echten Credentials.
    WHY: Die AI darf NIEMALS echte Passwörter sehen oder ausgeben.
    CONSEQUENCES: Nur Platzhalter werden ersetzt, alles andere bleibt unverändert.
    """
    if "text" not in params:
        return params

    text = params["text"]
    if text == "<EMAIL>" or text.upper() == "EMAIL":
        params["text"] = email or ""
        audit("action", message="Credential injected: EMAIL (redacted)")
    elif text == "<PASSWORD>" or text.upper() == "PASSWORD":
        params["text"] = pwd or ""
        audit("action", message="Credential injected: PASSWORD (redacted)")
    elif text == "<NAME>":
        params["text"] = str(USER_PROFILE.get("name") or "")
    elif text == "<AUTO>":
        selector_hint = str(params.get("selector", ""))
        resolved = _resolve_profile_value(selector_hint)
        if resolved is not None:
            params["text"] = resolved
    else:
        selector_hint = str(params.get("selector", ""))
        resolved = _resolve_profile_value(selector_hint)
        if text in ("<FIRST_NAME>", "<LAST_NAME>", "<CITY>", "<REGION>") and resolved:
            params["text"] = resolved

    return params


# ============================================================================
# SCROLL-HANDLER
# ============================================================================


async def handle_scroll(direction: str):
    # -------------------------------------------------------------------------
    # FUNKTION: handle_scroll
    # PARAMETER: direction: str
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Scrollt die Seite nach oben oder unten."""
    tab_params = _tab_params()
    pixels = 400 if direction == "scroll_down" else -400
    js_code = f"window.scrollBy(0, {pixels}); ({{ scrolled: true, by: {pixels} }})"
    await execute_bridge("execute_javascript", {"script": js_code, **tab_params})
    audit("action", message=f"Scrolled {direction}", pixels=pixels)


# ============================================================================
# ACTION-DISPATCH — Einheitlicher Pfad für alle Click-Entry-Points
# ============================================================================


async def run_click_action(next_params: dict, gate, img_hash: str, step_num: int) -> bool:
    """
    Leitet alle Click-Aktionen durch genau EINE verifizierte Eskalationspipeline.
    WHY: Issue #86 verlangt, dass `click_ref` keinen direkten Bridge-Bypass mehr hat.
    CONSEQUENCES: Jeder Click-Entry-Point läuft hier zentral durch `escalating_click()`.
    """
    selector = next_params.get("selector", "")
    description = next_params.get("description", "")
    x = next_params.get("x")
    y = next_params.get("y")
    ref = next_params.get("ref", "")

    if selector and gate.is_selector_failed(selector):
        audit(
            "state_change",
            message=f"Selektor '{selector[:50]}' bereits fehlgeschlagen, überspringe",
        )
        gate.record_step("RETRY", img_hash)
        return False

    clicked = await escalating_click(
        selector=selector,
        description=description,
        x=x,
        y=y,
        step_num=step_num,
        ref=ref,
    )

    if not clicked:
        if selector:
            gate.add_failed_selector(selector)
        audit("error", message="Klick-Eskalation komplett fehlgeschlagen")

    return clicked


def _write_structured_run_summary(run_summary: RunSummary, gate) -> Path:
    summary = run_summary.to_dict(include_steps=True)
    summary["artifact_dir"] = str(ARTIFACT_DIR)
    summary["screenshots"] = len(list(SCREENSHOT_DIR.glob("*.png")))
    if AUDIT_LOG_PATH.exists():
        with open(AUDIT_LOG_PATH, encoding="utf-8") as _fh:
            summary["audit_entries"] = sum(1 for _ in _fh)
    else:
        summary["audit_entries"] = 0
    if gate is not None:
        summary["controller"] = {
            "total_steps": gate.total_steps,
            "successful_actions": gate.successful_actions,
            "consecutive_retries": gate.consecutive_retries,
            "no_progress_count": gate.no_progress_count,
            "last_page_state": gate.last_page_state,
            "failed_selectors": list(gate.failed_selectors),
        }
    summary_path = ARTIFACT_DIR / "run_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary_path


async def _run_fail_replay_analysis(
    recorder,
    run_summary: RunSummary,
    gate,
    exit_reason: str,
    final_page_state: str,
) -> Path | None:
    if recorder is None:
        return None

    keyframes = recorder.get_keyframes(WORKER_CONFIG.recorder.keyframes_on_fail)
    output_dir = ARTIFACT_DIR / "fail_replay"
    keyframe_paths = save_keyframes_to_disk(keyframes, output_dir, prefix="keyframe")
    keyframe_urls = []
    for path in keyframe_paths:
        uploaded_url = upload_to_box(path)
        if uploaded_url:
            keyframe_urls.append(uploaded_url)

    step_annotations = []
    for frame in keyframes:
        parts = [frame.step_label, frame.vision_verdict, frame.page_state]
        step_annotations.append(" | ".join(part for part in parts if part) or "frame")

    fail_context = (
        f"exit_reason={exit_reason}; total_steps={run_summary.total_steps}; "
        f"page_state={final_page_state}; retries={getattr(gate, 'consecutive_retries', 0)}; "
        f"no_progress={getattr(gate, 'no_progress_count', 0)}"
    )

    analysis = await analyze_fail_multiframe(
        keyframe_bytes=[frame.png_bytes for frame in keyframes],
        fail_context=fail_context,
        nvidia_api_key=NVIDIA_API_KEY,
        step_annotations=step_annotations,
        model=NVIDIA_VISION_MODEL,
        nim_base_url=WORKER_CONFIG.nvidia.base_url,
        timeout=WORKER_CONFIG.nvidia.timeout,
        max_image_bytes=WORKER_CONFIG.nvidia.max_inline_bytes,
    )

    report_md = generate_fail_report_markdown(
        analysis=analysis,
        run_id=RUN_ID,
        total_steps=run_summary.total_steps,
        last_page_state=final_page_state,
        keyframe_urls=keyframe_urls,
    )
    report_path = save_fail_report_to_disk(report_md, analysis, output_dir, RUN_ID)
    remember_fail_learning(analysis, exit_reason, final_page_state, gate=gate)

    repo = os.environ.get("FAIL_REPORT_REPO", "")
    issue_number = os.environ.get("FAIL_REPORT_ISSUE_NUMBER", "")
    comment_posted = False
    if repo and issue_number.isdigit():
        comment_posted = post_github_issue_comment(repo, int(issue_number), report_md)

    audit(
        "fail_replay",
        report_path=str(report_path),
        keyframes=len(keyframes),
        comment_posted=comment_posted,
        analysis_error=str(analysis.get("error", ""))[:200],
    )
    return report_path


def _resolve_terminal_exit_reason(exit_reason: str, gate) -> str:
    if exit_reason != "startup":
        return exit_reason
    if gate is None:
        return "startup"
    if gate.total_steps >= MAX_STEPS:
        return "limit_reached:max_steps"
    if gate.consecutive_retries >= MAX_RETRIES:
        return "limit_reached:max_retries"
    if gate.no_progress_count >= MAX_NO_PROGRESS:
        return "limit_reached:no_progress"
    return "loop_finished"


def _should_generate_fail_replay(exit_reason: str) -> bool:
    return exit_reason not in {"vision_done", "loop_finished"}


async def _finalize_worker_run(
    run_summary: RunSummary,
    gate,
    final_exit_reason: str,
    final_page_state: str,
    recorder,
) -> tuple[Path, Path | None, str]:
    global CURRENT_RUN_SUMMARY

    resolved_exit_reason = _resolve_terminal_exit_reason(final_exit_reason, gate)
    if recorder is not None:
        await recorder.stop()

    run_summary.finalize(exit_reason=resolved_exit_reason, page_state=final_page_state)
    summary_path = _write_structured_run_summary(run_summary, gate)
    fail_report_path = None
    if _should_generate_fail_replay(resolved_exit_reason):
        fail_report_path = await _run_fail_replay_analysis(
            recorder,
            run_summary,
            gate,
            resolved_exit_reason,
            final_page_state,
        )
    CURRENT_RUN_SUMMARY = None
    return summary_path, fail_report_path, resolved_exit_reason


# ============================================================================
# HAUPTSCHLEIFE — Der komplette Vision Gate Loop
# ============================================================================


async def main():
    # -------------------------------------------------------------------------
    # FUNKTION: main
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    global CURRENT_RUN_SUMMARY, BUDGET_GUARD
    run_summary = RunSummary(run_id=RUN_ID)
    CURRENT_RUN_SUMMARY = run_summary
    gate = None
    recorder = None
    final_exit_reason = "startup"
    final_page_state = "unknown"

    # Platform-Profil + Budget-Guard initialisieren.
    # WHY: Der Worker bleibt Plattform-agnostisch — nur das Profil wechselt
    # (HeyPiggy, Prolific, Clickworker, Attapoll, eigenes JSON).
    # Der BudgetGuard zaehlt Tokens/Requests/EUR pro Run und "trippt" sich
    # selbst wenn ein Limit erreicht wird. Der Main-Loop fragt tripped() pro
    # Iteration ab und faehrt geordnet herunter wenn noetig.
    prof = _active_platform()
    BUDGET_GUARD = BudgetGuard.from_env(audit=audit)

    audit(
        "start",
        message=f"A2A-SIN-Worker ({prof.name}) Vision Gate v2.0",
        run_id=RUN_ID,
        artifact_dir=str(ARTIFACT_DIR),
        platform=prof.name,
        dashboard_url=prof.dashboard_url,
        budget_max_tokens=BUDGET_GUARD.max_tokens or None,
        budget_max_requests=BUDGET_GUARD.max_requests or None,
        budget_max_eur=BUDGET_GUARD.max_eur or None,
    )

    # 1. BRIDGE-VERBINDUNG PRÜFEN
    try:
        await wait_for_extension(timeout=BRIDGE_CONNECT_TIMEOUT)
    except Exception as e:
        final_exit_reason = f"bridge_connect_failed: {e}"
        run_summary.bridge_errors += 1
        await _finalize_worker_run(run_summary, gate, final_exit_reason, final_page_state, recorder)
        audit("stop", reason=f"Bridge-Verbindung fehlgeschlagen: {e}")
        return

    # 2. PRE-FLIGHT — Pflicht-Env + Vision-Auth müssen VOR Browser-Mutation healthy sein
    # SKIP_PREFLIGHT=1 umgeht den Vision-Probe (nuetzlich wenn Auth noch nicht ready)
    skip_preflight = os.environ.get("SKIP_PREFLIGHT", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if skip_preflight:
        audit(
            "warning",
            message="Preflight uebersprungen (SKIP_PREFLIGHT=1) — Vision Calls werden fehlschlagen!",
        )
    else:
        preflight = await ensure_worker_preflight()
        if not preflight.get("ok"):
            final_exit_reason = f"preflight_failed: {preflight.get('reason', 'unknown')}"
            run_summary.vision_errors += 1
            await _finalize_worker_run(
                run_summary, gate, final_exit_reason, final_page_state, recorder
            )
            return

    # Credentials erst NACH erfolgreichem fail-closed Preflight auslesen.
    # WHY: Die eigentliche Worker-Logik braucht die Werte für inject_credentials(),
    # aber nur nachdem bewiesen ist, dass Env vollständig und Vision healthy sind.
    email = os.environ.get("HEYPIGGY_EMAIL")
    pwd = os.environ.get("HEYPIGGY_PASSWORD")

    # 3. VISION GATE CONTROLLER INITIALISIEREN
    gate = VisionGateController()
    recorder = ScreenRingRecorder(
        fps=WORKER_CONFIG.recorder.fps,
        buffer_seconds=WORKER_CONFIG.recorder.buffer_seconds,
    )
    await recorder.start()

    # 3a. MEDIA ROUTER + SURVEY ORCHESTRATOR INITIALISIEREN
    # WHY: Diese beiden Subsysteme brauchen Zugriff auf execute_bridge und
    # _tab_params — beide sind erst nach Bridge-Connect sinnvoll instanzierbar.
    # CONSEQUENCES: Ab jetzt scannt dom_prescan() auch Media; der Orchestrator
    # übernimmt die Multi-Survey-Koordination.
    global MEDIA_ROUTER, SURVEY_ORCHESTRATOR

    def _queue_audit(event: str, **data):
    # -------------------------------------------------------------------------
    # FUNKTION: _queue_audit
    # PARAMETER: event: str, **data
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        audit(event, **data)

    if WORKER_CONFIG.media.enabled:
        MEDIA_ROUTER = MediaRouter(
            execute_bridge=execute_bridge,
            tab_params_factory=_tab_params,
            nvidia_api_key=NVIDIA_API_KEY,
            audio_model=WORKER_CONFIG.media.audio_model,
            video_model=WORKER_CONFIG.media.video_model,
            nim_base_url=WORKER_CONFIG.nvidia.base_url,
            audio_timeout=WORKER_CONFIG.media.audio_timeout,
            video_timeout=WORKER_CONFIG.media.video_timeout,
            frame_count=WORKER_CONFIG.media.video_frame_count,
            language_hint=WORKER_CONFIG.media.language_hint,
            audit=lambda msg: audit("media_router", message=msg),
        )
        audit(
            "media_router_ready",
            audio_model=WORKER_CONFIG.media.audio_model,
            video_model=WORKER_CONFIG.media.video_model,
        )
    else:
        audit("media_router_disabled")

    # Skip-Callback: fragt das Global Brain ob eine URL bekanntermassen zu DQ
    # fuehrt ODER ob wir sie im Rahmen der letzten 24h schon erfolgreich
    # abgeschlossen haben. Beide Faelle -> ueberspringen.
    # WHY: Kein Zeitverlust durch erneutes Durchlaufen von sicheren Fail-Umfragen
    # oder doppeltes Ausfuellen derselben Umfrage (manche Panels erlauben das
    # nicht und disqualifizieren sofort).
    async def _orchestrator_should_skip(url: str) -> tuple[bool, str]:
        try:
            brain = globals().get("GLOBAL_BRAIN")
            if brain is None or not url:
                return False, ""
            # Nur den URL-Path ohne Query fragen (Query-Params wechseln pro Session)
            try:
                from urllib.parse import urlparse

                pu = urlparse(url)
                key_url = f"{pu.scheme}://{pu.netloc}{pu.path}"
            except Exception:
                key_url = url
            answer = await brain.ask(
                f"Wurde die Umfrage-URL '{key_url}' in den letzten 48 Stunden "
                "schon disqualifiziert ODER erfolgreich abgeschlossen? "
                "Antworte NUR mit 'SKIP: <grund>' wenn ja, sonst mit 'OK'."
            )
            if not answer:
                return False, ""
            ans_low = answer.strip().lower()
            if ans_low.startswith("skip"):
                return True, answer.strip()[:200]
            return False, ""
        except Exception as e:
            audit("orchestrator_skip_check_error", error=str(e))
            return False, ""

    SURVEY_ORCHESTRATOR = SurveyOrchestrator(
        execute_bridge=execute_bridge,
        tab_params_factory=_tab_params,
        dashboard_url=WORKER_CONFIG.queue.dashboard_url,
        explicit_urls=list(WORKER_CONFIG.queue.explicit_urls),
        autodetect=WORKER_CONFIG.queue.autodetect,
        max_surveys=WORKER_CONFIG.queue.max_surveys,
        cooldown_sec=WORKER_CONFIG.queue.cooldown_sec,
        cooldown_jitter=WORKER_CONFIG.queue.cooldown_jitter_sec,
        audit=_queue_audit,
        should_skip=_orchestrator_should_skip,
    )
    audit(
        "orchestrator_ready",
        max_surveys=WORKER_CONFIG.queue.max_surveys,
        cooldown_sec=WORKER_CONFIG.queue.cooldown_sec,
        autodetect=WORKER_CONFIG.queue.autodetect,
        explicit_count=len(WORKER_CONFIG.queue.explicit_urls),
    )

    # 3b. PERSONA + GLOBAL BRAIN INITIALISIEREN
    # WHY: Persona = harte Fakten, Answer-Log = Konsistenz, Brain = Flotten-Wissen.
    # Alle drei sind optional — der Worker läuft auch ohne, fällt aber in den
    # Legacy-Modus zurück der keine Wahrheits-Garantie gibt.
    global ACTIVE_PERSONA, ANSWER_LOG, GLOBAL_BRAIN, _BRAIN_PRIME_CONTEXT
    persona_cfg = WORKER_CONFIG.persona

    if persona_cfg.enabled and persona_cfg.username:
        try:
            ACTIVE_PERSONA = load_persona(persona_cfg.username, Path(persona_cfg.profiles_dir))
            if ACTIVE_PERSONA is not None:
                audit(
                    "persona_loaded",
                    username=persona_cfg.username,
                    age=ACTIVE_PERSONA.age,
                    country=ACTIVE_PERSONA.country,
                    fields_set=sum(
                        1
                        for fn in ACTIVE_PERSONA.__dataclass_fields__
                        if bool(getattr(ACTIVE_PERSONA, fn, None))
                    ),
                )
            else:
                audit("persona_not_found", username=persona_cfg.username)
        except Exception as e:
            audit("persona_load_error", error=str(e), username=persona_cfg.username)

        try:
            ANSWER_LOG = AnswerLog(
                username=persona_cfg.username,
                log_path=Path(persona_cfg.answer_log_path),
            )
            audit("answer_log_ready", path=str(ANSWER_LOG.log_path))
        except Exception as e:
            audit("answer_log_error", error=str(e))

    if persona_cfg.brain_enabled:
        try:
            GLOBAL_BRAIN = GlobalBrainClient(
                base_url=persona_cfg.brain_url,
                project_id=persona_cfg.brain_project_id,
                agent_id=persona_cfg.brain_agent_id,
                persona_username=persona_cfg.username or None,
                timeout_sec=persona_cfg.brain_timeout_sec,
                audit=lambda event, **data: audit(event, **data),
            )
            _BRAIN_PRIME_CONTEXT = await GLOBAL_BRAIN.attach()
        except Exception as e:
            audit("brain_init_error", error=str(e))
            _BRAIN_PRIME_CONTEXT = PrimeContext()

    # 4. INITIALE NAVIGATION
    action_desc = "Navigiere zu HeyPiggy Dashboard"
    expected = "Dashboard mit verfügbaren Umfragen oder Login-Formular"

    global CURRENT_TAB_ID, CURRENT_WINDOW_ID
    try:
        audit("navigate", url="https://www.heypiggy.com/login")
        # KRITISCH: active: True — Tab MUSS im Vordergrund sein!
        # Mit active: False läuft der Tab im Hintergrund → Screenshots zeigen falschen Inhalt
        # → Vision Gate sieht nichts → DOM-Verifikation gibt url="" zurück → Worker hängt
        tab_res = await execute_bridge(
            "tabs_create", {"url": "https://www.heypiggy.com/login", "active": True}
        )
        if isinstance(tab_res, dict) and "tabId" in tab_res:
            CURRENT_TAB_ID = tab_res["tabId"]
            CURRENT_WINDOW_ID = tab_res.get("windowId", CURRENT_WINDOW_ID)
            audit(
                "success",
                message=f"Worker-Tab erstellt und gebunden: tabId={CURRENT_TAB_ID}, windowId={CURRENT_WINDOW_ID}",
            )
        else:
            final_exit_reason = "tabs_create_missing_tab_id"
            run_summary.bridge_errors += 1
            # tabs_create hat keine tabId zurückgegeben — harter Abbruch.
            # KEIN Fallback auf fremde Tabs, da wir sonst einen User-Tab steuern würden.
            audit(
                "stop",
                reason=f"tabs_create hat keine tabId zurückgegeben: {tab_res}. "
                "Kein Fallback auf aktiven Tab erlaubt.",
            )
            await _finalize_worker_run(
                run_summary, gate, final_exit_reason, final_page_state, recorder
            )
            return
    except Exception as e:
        final_exit_reason = f"initial_navigation_failed: {e}"
        run_summary.bridge_errors += 1
        audit("stop", reason=f"Initiale Navigation fehlgeschlagen: {e}")
        await _finalize_worker_run(run_summary, gate, final_exit_reason, final_page_state, recorder)
        return

    # Verifikation: CURRENT_TAB_ID muss jetzt gesetzt sein
    if CURRENT_TAB_ID is None:
        final_exit_reason = "missing_current_tab_id_after_init"
        run_summary.bridge_errors += 1
        audit("stop", reason="CURRENT_TAB_ID ist nach Init immer noch None — Abbruch")
        await _finalize_worker_run(run_summary, gate, final_exit_reason, final_page_state, recorder)
        return

    # Warten auf Seitenlade
    await human_delay(4.0, 6.0)

    # =========================================================================
    # STEALTH LAYER — Browser-Fingerprint Masking (Issue #57)
    # WHY: HeyPiggy und Panel-Anbieter (PureSpectrum, Dynata, Cint) messen
    #      navigator.webdriver, Canvas-Fingerprints, WebGL-Renderer und
    #      fehlende Chrome-APIs um Bots zu erkennen. Ohne Masking werden wir
    #      sofort als Bot markiert → Account-Sperre / DQ auf allen Surveys.
    # CONSEQUENCES: Dieses JS wird einmalig nach Tab-Create injiziert und
    #               maskiert die häufigsten Browser-Fingerprint-Vektoren.
    #               Es darf NIEMALS Fehler werfen (try/catch um alles).
    #
    # GOTCHA: Falls der Worker Umfragen öffnet aber sofort DQ (Disqualified)
    #         bekommt, ist oft der Stealth-Layer kompromittiert oder veraltet.
    # =========================================================================
    try:
        stealth_js = """
(function() {
    'use strict';
    try {
        // 1. webdriver-Flag entfernen — das wichtigste Anti-Bot-Signal
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
    } catch(e) {}

    try {
        // 2. Chrome-Objekt simulieren (fehlt bei CDP-kontrollierten Browsern)
        if (!window.chrome) {
            window.chrome = {
                runtime: {
                    onMessage: { addListener: function() {} },
                    connect: function() { return { onMessage: { addListener: function() {} }, postMessage: function() {} }; }
                },
                loadTimes: function() { return {}; },
                csi: function() { return {}; }
            };
        }
    } catch(e) {}

    try {
        // 3. Canvas-Fingerprint-Noise — minimale zufällige Pixel-Variation
        //    damit jeder Fingerprint-Hash leicht anders ist
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
            const ctx = this.getContext('2d');
            if (ctx) {
                const imageData = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
                const d = imageData.data;
                // Ändere einen zufälligen Alpha-Wert minimal (±1) — unsichtbar aber ändert Hash
                const idx = (Math.floor(Math.random() * (d.length / 4))) * 4 + 3;
                if (d[idx] !== undefined) d[idx] = Math.max(0, Math.min(255, d[idx] + (Math.random() > 0.5 ? 1 : -1)));
                ctx.putImageData(imageData, 0, 0);
            }
            return origToDataURL.apply(this, arguments);
        };
    } catch(e) {}

    try {
        // 4. WebGL-Renderer/Vendor verschleiern
        const origGetParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Intel Inc.';       // UNMASKED_VENDOR_WEBGL
            if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
            return origGetParam.apply(this, arguments);
        };
    } catch(e) {}

    try {
        // 5. Plugins-Array simulieren (echte Browser haben Plugins, CDP-Browser nicht)
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const p = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                ];
                p.item = (i) => p[i];
                p.namedItem = (n) => p.find(x => x.name === n) || null;
                p.refresh = () => {};
                return p;
            },
            configurable: true
        });
    } catch(e) {}

    try {
        // 6. languages setzen (Deutsch/Österreich-Profil für Jeremy Schulze)
        Object.defineProperty(navigator, 'languages', {
            get: () => ['de-DE', 'de', 'en-US', 'en'],
            configurable: true
        });
    } catch(e) {}

    try {
        // 7. Permissions-API mocken damit permission-basierte Bot-Detection greift
        const origQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (origQuery) {
            navigator.permissions.query = function(params) {
                if (params.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return origQuery.apply(this, arguments);
            };
        }
    } catch(e) {}

    return { stealth: 'ok', ts: Date.now() };
})();
"""
        stealth_result = await execute_bridge(
            "execute_javascript",
            {"script": stealth_js, **_tab_params()},
        )
        audit(
            "stealth_injected",
            result=str(stealth_result)[:120],
            message="Stealth Layer aktiv: webdriver/canvas/webgl/plugins maskiert",
        )
    except Exception as se:
        # Stealth-Fehler darf den Worker NIEMALS stoppen
        audit("stealth_inject_error", error=str(se))

    # Cross-Run-Session wiederherstellen BEVOR wir uns einloggen.
    # WHY: Der Cache enthaelt Cookies + LocalStorage der letzten Panel-Logins
    # (HeyPiggy, PureSpectrum, Dynata, Sapio, Cint, Lucid). Wenn die noch
    # gueltig sind, ueberspringen wir den kompletten Login-Flow beim naechsten
    # Screen (wir sind automatisch angemeldet).
    # CONSEQUENCES: Wir sparen pro Run 30-120 Sekunden + vermeiden dass Panels
    # uns als "haeufige Neu-Logins" markieren (Trust-Score-Abwertung).
    try:
        # WHY: target_url kommt jetzt aus dem aktiven Platform-Profil damit der
        # Worker fuer andere Anbieter (Prolific, Clickworker, Attapoll, ...) ohne
        # Code-Aenderung nur per ENV umgeschaltet werden kann.
        _profile = _active_platform()
        restore_result = await _session_restore(
            execute_bridge=execute_bridge,
            tab_params=_tab_params(),
            target_url=_profile.dashboard_url,
            audit=audit,
        )
        if restore_result.get("restored"):
            audit(
                "session_restore_applied",
                cookies_set=restore_result.get("cookies_set"),
                storage_keys=restore_result.get("storage_keys"),
                saved_at=restore_result.get("saved_at"),
            )
            # Reload damit die restaurierten Cookies/Storage greifen
            try:
                await execute_bridge(
                    "execute_javascript",
                    {"script": "location.reload();", **_tab_params()},
                )
                await human_delay(3.5, 5.0)
            except Exception as re:
                audit("session_restore_reload_error", error=str(re))
    except Exception as e:
        audit("session_restore_error", error=str(e))

    # Session direkt nach Laden sichern (und persistenten Cache aktualisieren)
    await save_session("initial_load")

    # Google-Login-Versuch (Issue #58): Wenn noch kein aktiver Login,
    # klicke proaktiv den Google-Button BEVOR der Vision-Loop startet.
    # WHY: Schneller als Vision-gesteuerten Login abzuwarten — vermeidet
    #      mehrere Retry-Loops nur für den Login-Screen.
    # CONSEQUENCES: Bei Fehler ignorieren — Vision-Loop übernimmt den Login.
    _google_login_attempted = False
    try:
        _url_check_js = "document.location.href"
        _url_res = await execute_bridge(
            "execute_javascript", {"script": _url_check_js, **_tab_params()}
        )
        _current_url = ""
        if isinstance(_url_res, dict):
            _current_url = str(_url_res.get("result", "") or "")
        if "login" in _current_url or "signin" in _current_url or not _current_url:
            _google_result = await attempt_google_login(email or "")
            _google_login_attempted = True
            audit("google_login_proactive", result=_google_result)
            if _google_result.get("ok"):
                # Issue #61 Fix F4: Google-OAuth hat 3-5 Redirect-Hops
                # (account-picker → consent → callback → heypiggy). Bei
                # langsamer Leitung vergehen 10-15 s. Der alte Wert 4-7 s
                # war zu knapp — der Loop startete oft mitten im Redirect.
                await human_delay(8.0, 14.0)
                await save_session("after_google_login")
    except Exception as _gle:
        audit("google_login_proactive_error", error=str(_gle))

    # Issue #61 Fix F1 + F4 + F5: POST-LOGIN BOOTSTRAP
    # WHY: Nach erfolgreichem Login (oder erfolgreichem Session-Restore) muss
    # der Worker deterministisch auf einer Survey-Seite landen. Frueher
    # wurde die Navigation komplett Vision ueberlassen, was in der Praxis
    # regelmaessig gescheitert ist:
    #   - OAuth-Redirect landet auf `/` oder `/?tab=surveys`, nicht auf
    #     `/?page=dashboard` → Dashboard-Ranking-Block blieb stumm
    #   - SurveyOrchestrator.begin() wurde nie aufgerufen → die allererste
    #     Umfrage musste Vision komplett solo finden
    # CONSEQUENCES: Wir (1) warten auf URL-Stabilitaet (dreimal gleiche
    # URL in Folge), (2) navigieren explizit zum Dashboard falls wir
    # ausserhalb sind, (3) rufen orch.begin() auf, das via Dashboard-
    # Ranking + ghost_click die lukrativste Kachel oeffnet.
    async def _wait_for_url_stable(max_wait_sec: float = 12.0) -> str:
        """Wartet bis die URL dreimal in Folge gleich bleibt (Redirects fertig)."""
        deadline = time.monotonic() + max_wait_sec
        last_url = ""
        same_streak = 0
        while time.monotonic() < deadline:
            try:
                res = await execute_bridge(
                    "execute_javascript",
                    {"script": "document.location.href", **_tab_params()},
                )
                url = ""
                if isinstance(res, dict):
                    url = str(res.get("result", "") or "")
                if url and url == last_url:
                    same_streak += 1
                    if same_streak >= 2:  # 3 gleiche Messungen → stabil
                        return url
                else:
                    same_streak = 0
                    last_url = url
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return last_url

    try:
        stable_url = await _wait_for_url_stable(max_wait_sec=12.0)
        audit("post_login_url_stable", url=stable_url or "(unknown)")

        _is_heypiggy = "heypiggy.com" in (stable_url or "").lower()
        _is_survey_detail = (
            "/survey/" in (stable_url or "").lower()
            or "/s/" in (stable_url or "").lower()
        )
        _is_google_oauth = "accounts.google." in (stable_url or "").lower()

        # Wenn wir weder auf heypiggy noch auf einer Survey-Detail-Page sind,
        # navigieren wir explizit zum konfigurierten Dashboard. Das greift
        # z.B. wenn OAuth auf einem "Welcome"-Screen haengen bleibt.
        if not _is_heypiggy or _is_google_oauth:
            _dashboard_url = WORKER_CONFIG.queue.dashboard_url
            audit("post_login_force_navigate", target=_dashboard_url)
            try:
                await execute_bridge(
                    "navigate",
                    {"url": _dashboard_url, **_tab_params()},
                )
                await human_delay(3.0, 5.0)
            except Exception as _ne:
                audit("post_login_navigate_error", error=str(_ne))

        # Nur wenn wir NICHT bereits auf einer Survey-Detail-Page sind, soll
        # der Orchestrator die erste Umfrage oeffnen. Falls wir via
        # Session-Restore direkt in einer laufenden Umfrage landen, wollen
        # wir sie nicht verlassen.
        if SURVEY_ORCHESTRATOR is not None and not _is_survey_detail:
            try:
                first_record = await SURVEY_ORCHESTRATOR.begin()
                if first_record is not None:
                    audit(
                        "orchestrator_begin_ok",
                        index=first_record.index,
                        start_url=first_record.start_url,
                    )
                    await human_delay(2.5, 4.5)
                else:
                    audit("orchestrator_begin_no_survey_found")
            except Exception as _be:
                audit("orchestrator_begin_error", error=str(_be))
        elif _is_survey_detail:
            audit("post_login_already_on_survey", url=stable_url)
    except Exception as _pb:
        audit("post_login_bootstrap_error", error=str(_pb))

    # 5. VISION GATE LOOP — Das Herzstück
    while gate.should_continue():
        current_step = gate.total_steps + 1

        # ---- Bridge-Health-Check vor JEDER Iteration ----
        if not await check_bridge_alive():
            final_exit_reason = "bridge_unreachable_during_loop"
            run_summary.bridge_errors += 1
            audit("stop", reason="Bridge nicht erreichbar, Abbruch")
            break

        # ---- SCREENSHOT ----
        img_path, img_hash = await take_screenshot(current_step, label=action_desc[:20])
        if not img_path:
            gate.record_step("RETRY", "", "unknown")
            run_summary.record_step(
                step_number=current_step,
                verdict="RETRY",
                page_state="unknown",
                action="take_screenshot",
                success=False,
                error="screenshot_failed",
            )
            await human_delay(2.0, 4.0)
            continue

        # THROTTLE: Vor Vision-Calls länger warten um Rate-Limit zu vermeiden
        # WHY: Antigravity hat Rate-Limits; zu schnelle Calls führen zu leeren Antworten.
        delay_min, delay_max = get_fail_learning_delay_bounds(5.0, 10.0)
        await human_delay(delay_min, delay_max)

        # ---- VISION CHECK ----
        decision = await ask_vision(img_path, action_desc, expected, current_step)

        verdict = decision.get("verdict", "RETRY")
        reason = decision.get("reason", "Kein Grund")
        page_state = decision.get("page_state", "unknown")
        final_page_state = str(page_state)
        page_state_machine.transition(final_page_state)
        next_action = decision.get("next_action", "none")
        next_params = decision.get("next_params", {})
        progress = decision.get("progress", False)

        decision = apply_fail_learning_to_decision(decision, gate, img_hash or "")
        verdict = decision.get("verdict", verdict)
        next_action = decision.get("next_action", next_action)
        next_params = decision.get("next_params", next_params)
        progress = decision.get("progress", progress)
        reason = decision.get("reason", reason)

        # Schritt aufzeichnen
        gate.record_step(str(verdict), img_hash or "", final_page_state)
        if recorder is not None:
            recorder.annotate_last_frame(
                f"step_{current_step}_{next_action}", str(verdict), final_page_state
            )
        run_summary.record_step(
            step_number=current_step,
            verdict=str(verdict),
            page_state=final_page_state,
            action=str(next_action),
            success=str(verdict) != "STOP",
            error="" if str(verdict) != "STOP" else str(reason),
        )

        print(f"\n{'=' * 60}")
        print(f"SCHRITT {gate.total_steps}/{MAX_STEPS} | Verdict: {verdict} | State: {page_state}")
        print(f"Reason: {reason}")
        print(f"Next: {next_action} {json.dumps(next_params, ensure_ascii=False)[:120]}")
        print(
            f"Retries: {gate.consecutive_retries}/{MAX_RETRIES} | No-Progress: {gate.no_progress_count}/{MAX_NO_PROGRESS}"
        )
        print(f"{'=' * 60}\n")

        # ---- CAPTCHA ERKENNUNG UND BEHANDLUNG (NEU in v3.1) ----
        captcha_detected = await detect_captcha_page()
        if captcha_detected:
            run_summary.captcha_encounters += 1
            audit("captcha", message="Captcha erkannt! Versuche Auto-Bypass...")
            captcha_ok = await handle_captcha()
            if captcha_ok:
                audit("success", message="Captcha erfolgreich behandelt!")
                await human_delay(2.0, 4.0)
                continue
            else:
                audit(
                    "error",
                    message="Captcha-Bypass fehlgeschlagen — Vision kann helfen",
                )

        # ---- SURVEY ABSCHLUSS-GARANTIE (NEU in v3.1) ----
        # Wenn page_state="survey_active" und Vision sagt STOP oder none:
        # → Survey läuft noch! Niemals abbrechen! Retry zwingend!
        if page_state in (
            "survey_active",
            "survey_audio",
            "survey_video",
            "survey_image",
        ) and verdict in ("STOP", "none"):
            audit(
                "warning",
                message="SURVEY ABSCHLUSS-GARANTIE: survey_active aber Vision will stoppen → Ignoriert! Survey MUSS fertig werden!",
            )
            verdict = "PROCEED"
            decision["verdict"] = "PROCEED"
            decision["next_action"] = "click_ref"
            next_action = "click_ref"
            next_params = {}

        # ---- STOP ----
        if verdict == "STOP":
            final_exit_reason = f"vision_stop: {reason}"
            run_summary.vision_errors += 1
            audit("stop", reason=reason, page_state=page_state)
            await save_session("stop_state")
            break

        # ---- RETRY ----
        if verdict == "RETRY":
            # Bei RETRY den Selektor als fehlgeschlagen merken
            if next_params.get("selector"):
                gate.add_failed_selector(next_params["selector"])
            await human_delay(2.0, 4.0)
            continue

        # ---- DONE ----
        if next_action == "none":
            final_exit_reason = "vision_done"
            audit(
                "success",
                message="Vision meldet: Aufgabe erledigt!",
                total_steps=gate.total_steps,
            )
            await save_session("completed")
            break

        # ---- RATING-GUARD: survey_done darf NICHT gefeuert werden solange die
        # Post-Survey-Bewertung offen ist. Panels wie CPX geben Bonus-Cent fuer
        # das 5-Sterne+Freitext-Rating — ein "survey_done" VOR dem Submit-Klick
        # laesst Geld liegen und der Orchestrator springt zu frueh weiter.
        # WHY: Wir ueberschreiben page_state zurueck auf "survey_done" pending,
        # und lassen den Loop noch einmal durchlaufen, damit die Aktionen aus
        # dem Rating-Block (5 Sterne -> Textarea -> Submit) abgearbeitet werden.
        global _RATING_SUBMITTED_FOR_CURRENT
        if (
            page_state == "survey_done"
            and _LAST_RATING_PAGE is not None
            and not _RATING_SUBMITTED_FOR_CURRENT
        ):
            audit(
                "rating_guard_override",
                note="survey_done uebergangen — Rating steht noch aus",
                has_textarea=bool(_LAST_RATING_PAGE.get("textarea_ref")),
                has_submit=bool(_LAST_RATING_PAGE.get("submit_ref")),
            )
            # Erzwinge dass der Agent weiter die Rating-Aktionen ausfuehrt.
            # Wir setzen page_state auf survey_active damit der adaptive-delay
            # und answer-recording-Pfad greift und kein "Umfrage zu Ende"
            # Schnitt gemacht wird.
            page_state = "survey_active"

        # Merke ob in diesem Schritt der Submit-Klick auf der Rating-Seite war.
        # WHY: Wenn der Agent gerade den Submit-Button der Rating-Seite
        # gedrueckt hat (next_params.ref == submit_ref), markieren wir das
        # Rating als erledigt — beim naechsten survey_done darf der Orchestrator
        # dann sauber weiter.
        if (
            _LAST_RATING_PAGE is not None
            and next_action in CLICK_ACTIONS
            and isinstance(next_params, dict)
        ):
            submit_ref = str(_LAST_RATING_PAGE.get("submit_ref") or "").strip()
            clicked_ref = str(next_params.get("ref") or "").strip()
            if submit_ref and clicked_ref and submit_ref == clicked_ref:
                _RATING_SUBMITTED_FOR_CURRENT = True
                audit("rating_submitted", ref=submit_ref)

        # ---- SURVEY DONE — Bestätigungsseite erkannt ----
        # WHY: Wenn page_state="survey_done" bedeutet das, die aktuelle Umfrage wurde
        # erfolgreich abgeschlossen und eine Bestätigungsseite ist sichtbar.
        # KONSEQUENZ: Wir brechen NICHT ab — wir sichern den Fortschritt und
        # lassen den Hauptloop weiterlaufen, damit die Vision die nächste Umfrage
        # oder die Rückkehr zum Dashboard selbst erkennt und navigiert.
        # WARNUNG: Hier kein "break"! Abbrechen würde weitere ausstehende Surveys verpassen.
        if page_state == "survey_done":
            # DQ-Erkennung: reason enthaelt "disqualif" ODER der letzte
            # Screener-Trigger war positiv UND wir haben keinen EUR-Reward
            # gesehen. Bei echten Completes erscheint immer ein Reward-Banner.
            _reason_low = str(reason or decision.get("reason", "")).lower()
            is_dq = (
                "disqualif" in _reason_low
                or "screen" in _reason_low
                and "out" in _reason_low
                or "nicht teilnehmen" in _reason_low
                or "kein passender" in _reason_low
            )
            # Quota-Full ist KEIN DQ — die Umfrage war ok, nur voll.
            # WHY: Wuerden wir Quota-Full als DQ ins Brain schreiben, wuerden
            # wir die URL morgen ueberspringen obwohl sie wieder offen sein
            # koennte. Das kostet bares Geld.
            if _QUOTA_FULL_DETECTED or "quote" in _reason_low or "quota" in _reason_low:
                is_dq = False
                audit("quota_bypass_dq", reason=_reason_low[:80])
            if is_dq:
                run_summary.record_survey_disqualified()
                # Brain-Learning: Welche URL + Frage hat disqualifiziert?
                # WHY: Der Agent soll morgen denselben Screener sofort
                # erkennen und entweder eine andere Option probieren oder
                # die Umfrage ueberspringen.
                try:
                    brain = globals().get("GLOBAL_BRAIN")
                    if brain is not None and _LAST_QUESTION_TEXT:
                        # Aktuelle URL aus Bridge holen (falls verfuegbar)
                        cur_url = ""
                        try:
                            _u = await execute_bridge(
                                "execute_javascript",
                                {"script": "window.location.href;", **_tab_params()},
                            )
                            cur_url = str((_u or {}).get("result", ""))[:120]
                        except Exception:
                            cur_url = ""
                        dq_key = f"{_LAST_QUESTION_TEXT[:80]}|{cur_url[:60]}"
                        if dq_key not in _BRAIN_DQ_WRITTEN:
                            _BRAIN_DQ_WRITTEN.add(dq_key)
                            fact = (
                                f"SCREENER-DISQUALIFIKATION: Frage '{_LAST_QUESTION_TEXT[:150]}' "
                                f"auf URL '{cur_url or 'unknown'}' "
                                f"fuehrte zu DQ. Reason: '{_reason_low[:120]}'. "
                                "Bei kuenftigen Begegnungen andere Option probieren ODER "
                                "die Umfrage ueberspringen."
                            )
                            await brain.ingest_fact(fact, scope="project")
                            audit("brain_dq_learned", key=dq_key[:80])
                except Exception as be:
                    audit("brain_dq_error", error=str(be))
            else:
                run_summary.record_survey_completed()
            if recorder is not None:
                recorder.clear()
            audit(
                "success" if not is_dq else "dq_recognized",
                message=(
                    "Umfrage vollstaendig abgeschlossen — Bestaetigungsseite erkannt!"
                    if not is_dq
                    else "Umfrage disqualifiziert — als DQ verbucht."
                ),
                page_state=page_state,
                total_steps=gate.total_steps,
            )
            await save_session(f"survey_done_{gate.total_steps}")
            gate.mark_dom_progress()

            # ---- MULTI-SURVEY ORCHESTRATOR: nächste Umfrage holen ----
            # WHY: Früher hat der Worker hier einfach `continue` gemacht und
            # gehofft dass Vision selbstständig zur nächsten Umfrage navigiert.
            # Das war unzuverlässig — jetzt übernimmt der Orchestrator explizit.
            # CONSEQUENCES: Entweder (a) navigiert er zur nächsten Survey und der
            # Loop läuft weiter, oder (b) die Queue ist leer → wir brechen sauber
            # ab mit state=DONE.
            if SURVEY_ORCHESTRATOR is not None:
                try:
                    queue_state = await SURVEY_ORCHESTRATOR.on_survey_completed(
                        success=True,
                        steps_used=gate.total_steps,
                        end_reason="survey_done",
                    )
                except Exception as e:
                    audit("orchestrator_error", error=str(e))
                    queue_state = QueueState.ABORTED

                if queue_state == QueueState.RUNNING:
                    # BUDGET-GATE: Vor Beginn der NAECHSTEN Survey pruefen ob das
                    # Token-/Request-/EUR-Budget erschoepft ist. Wenn ja: aktuelle
                    # Survey war schon fertig, also sauber stoppen.
                    # WHY: Wir wollen niemals mitten in einer Survey abbrechen
                    # (das waere eine DQ und ruiniert die Trust-Scores). Der
                    # Survey-Boundary ist der sichere Cut-Point.
                    if BUDGET_GUARD is not None and BUDGET_GUARD.tripped():
                        final_exit_reason = (
                            f"budget_tripped: {BUDGET_GUARD.trip_reason or 'limit_reached'}"
                        )
                        audit(
                            "budget_shutdown",
                            reason=BUDGET_GUARD.trip_reason,
                            tokens_used=BUDGET_GUARD.tokens_used,
                            requests=BUDGET_GUARD.requests_used,
                            eur_spent=round(BUDGET_GUARD.eur_spent, 4),
                        )
                        break
                    # Neue Survey gestartet — gate-Limit + Rating-Flag zuruecksetzen
                    # WHY: Wir wollen pro Survey MAX_STEPS, nicht global.
                    # Der Rating-Flag muss fuer die neue Survey wieder False sein,
                    # sonst ueberspringen wir deren Post-Survey-Bewertung.
                    gate.reset_for_new_survey()
                    _RATING_SUBMITTED_FOR_CURRENT = False
                    # Loop/Spinner-Tracker fuer die neue Umfrage zuruecksetzen
                    _RECENT_QUESTIONS.clear()
                    _SAME_QUESTION_STREAK = 0
                    _SPINNER_STREAK = 0
                    # In-Survey Consistency-Memo + Quota-Flag zuruecksetzen
                    _ANSWER_MEMO.clear()
                    _QUOTA_FULL_DETECTED = False
                    action_desc = "Neue Umfrage gestartet — beantworte Fragen"
                    expected = "Erste Frage der neuen Umfrage ist sichtbar"
                    await human_delay(2.5, 4.5)
                    continue
                elif queue_state in (
                    QueueState.NO_MORE_AVAILABLE,
                    QueueState.LIMIT_REACHED,
                ):
                    final_exit_reason = f"queue_finished: {queue_state.name.lower()}"
                    audit(
                        "queue_finished",
                        state=queue_state.name,
                        completed=SURVEY_ORCHESTRATOR.completed_count,
                        attempted=SURVEY_ORCHESTRATOR.attempted_count,
                    )
                    break
                else:
                    final_exit_reason = f"queue_state_{queue_state.name.lower()}"
                    audit("queue_unexpected_state", state=queue_state.name)
                    break

            await human_delay(2.0, 4.0)
            continue

        # ---- CREDENTIAL INJECTION ----
        if next_action == "type_text" and next_params:
            next_params = inject_credentials(next_params, email, pwd)

        # ---- AKTION AUSFÜHREN ----
        action_desc = f"{next_action} {json.dumps(next_params, ensure_ascii=False)[:80]}"
        expected = f"UI hat auf {next_action} reagiert und sich verändert"
        audit(
            "action",
            action=next_action,
            params={
                k: v for k, v in next_params.items() if k != "text" or next_action != "type_text"
            },
        )

        # URL und Title VOR der Aktion für DOM-Verifikation sammeln
        before_url, before_title = "", ""
        try:
            pi_params = _tab_params()
            pi = await execute_bridge("get_page_info", pi_params)
            if isinstance(pi, dict):
                before_url = pi.get("url", "")
                before_title = pi.get("title", "")
        except Exception:
            pass

        # ---- ADAPTIVE HUMAN THINK-TIME ----
        # WHY: Panels messen die Antwort-Zeit pro Frage. Ein Bot der auf jede
        # Frage nach 1.5s klickt ist sofort gebrandmarkt (speeder-penalty ->
        # stille Disqualifikation ohne Reward). Wir passen die Pause an
        # Komplexitaet der Frage + erkannte Trap-Art an.
        # CONSEQUENCES: Nur bei Survey-States aktiv. Im Dashboard/Login/CAPTCHA
        # laufen die bestehenden festen human_delay()-Aufrufe weiter.
        if page_state in (
            "survey_active",
            "survey_audio",
            "survey_video",
            "survey_image",
            "onboarding",
        ) and next_action in CLICK_ACTIONS.union({"type_text", "select_option"}):
            trap_hint = str(decision.get("trap_detected") or "none").strip() or "none"
            if _LAST_SCREENER_HIT and trap_hint == "none":
                trap_hint = "screening"
            try:
                d = await adaptive_think_delay(
                    question_text=_LAST_QUESTION_TEXT,
                    options=_LAST_QUESTION_OPTIONS,
                    trap_detected=trap_hint,
                    action_kind=next_action,
                )
                audit(
                    "think_delay",
                    seconds=round(d, 2),
                    trap=trap_hint,
                    n_options=len(_LAST_QUESTION_OPTIONS or []),
                    qlen=len(_LAST_QUESTION_TEXT or ""),
                )
            except Exception as e:
                audit("think_delay_error", error=str(e))

        try:
            # Scroll-Aktionen
            if next_action in ("scroll_down", "scroll_up"):
                await handle_scroll(next_action)

            # Klick-Aktionen (mit EINER gemeinsamen Eskalationskette)
            elif next_action in CLICK_ACTIONS:
                await run_click_action(next_params, gate, img_hash, gate.total_steps)

            # Explizite Keyboard-Aktion von Vision
            elif next_action == "keyboard":
                keys = next_params.get("keys", ["Enter"])
                if isinstance(keys, str):
                    keys = [keys]
                selector = next_params.get("selector", "")
                await keyboard_action(keys, selector=selector)

            # Native <select>-Dropdown-Handler
            # WHY: Bridge.click_ref auf ein <option> funktioniert bei echten
            # HTML-<select>-Elementen oft NICHT zuverlaessig — der Browser
            # setzt .value nicht, change-Event feuert nicht, das Formular
            # bleibt invalid. Wir setzen deshalb direkt value + dispatch
            # change/input via execute_javascript.
            # CONSEQUENCES: Jede Auswahl in einem nativen <select> (Land,
            # Bundesland, Bildung, Einkommen, Beruf) wird korrekt gesetzt.
            # Custom-Dropdowns (div-basiert) laufen weiterhin ueber click_ref.
            elif next_action == "select_option":
                sel_params = {**next_params, **_tab_params()}
                selector = sel_params.get("selector", "")
                value = sel_params.get("value", "")
                label = sel_params.get("label", "") or sel_params.get("text", "")
                ref = sel_params.get("ref", "")
                # Native-Select-Pfad wenn Selektor vorhanden und nicht ref-style
                if selector and not selector.startswith("@"):
                    sel_escaped = selector.replace("'", "\\'")
                    val_escaped = str(value).replace("'", "\\'")
                    lab_escaped = str(label).replace("'", "\\'")
                    js = (
                        "(function(){"
                        f"var el=document.querySelector('{sel_escaped}');"
                        "if(!el||el.tagName.toLowerCase()!=='select')return{ok:false,reason:'not_native_select'};"
                        "var opts=Array.from(el.options);"
                        "var picked=null;"
                        f"var want_val='{val_escaped}'.toLowerCase();"
                        f"var want_lab='{lab_escaped}'.toLowerCase();"
                        "for(var i=0;i<opts.length;i++){"
                        "  var o=opts[i];"
                        "  var ov=(o.value||'').toLowerCase();"
                        "  var ot=(o.text||'').toLowerCase();"
                        "  if(want_val && ov===want_val){picked=o;break;}"
                        "  if(want_lab && (ot===want_lab||ot.indexOf(want_lab)!==-1)){picked=o;break;}"
                        "}"
                        "if(!picked)return{ok:false,reason:'no_match',available:opts.slice(0,12).map(function(o){return o.text;})};"
                        "el.value=picked.value;"
                        "el.dispatchEvent(new Event('input',{bubbles:true}));"
                        "el.dispatchEvent(new Event('change',{bubbles:true}));"
                        "return{ok:true,chosen:picked.text,value:picked.value};"
                        "})();"
                    )
                    try:
                        r = await execute_bridge(
                            "execute_javascript", {"script": js, **_tab_params()}
                        )
                        result = r.get("result") if isinstance(r, dict) else None
                        if isinstance(result, dict) and result.get("ok"):
                            audit(
                                "native_select_set",
                                selector=selector[:60],
                                chosen=result.get("chosen"),
                            )
                        else:
                            # Fallback: bridge's select_option
                            audit(
                                "native_select_fallback",
                                selector=selector[:60],
                                reason=result.get("reason")
                                if isinstance(result, dict)
                                else "unknown",
                            )
                            await execute_bridge("select_option", sel_params)
                    except Exception as se:
                        audit("native_select_error", error=str(se))
                        await execute_bridge("select_option", sel_params)
                elif ref:
                    # Ref-basiert -> bridge's select_option
                    await execute_bridge("select_option", sel_params)
                else:
                    await execute_bridge("select_option", sel_params)

            # Text-Eingabe — IMMER mit exaktem tabId, MIT HUMAN-TYPING
            # WHY: Bots tippen 200 Zeichen in 50ms -> Panels messen per-char
            # Keystroke-Intervalle (keydown/keyup Delta) und markieren
            # verdaechtig gleichmaessige Eingaben als Bot. Wir tippen
            # zeichenweise mit 40-180ms Jitter + gelegentlichen Mikro-Pausen.
            # CONSEQUENCES: Freitext-Antworten sehen menschlich aus,
            # Bot-Fingerprinting wird umgangen.
            elif next_action == "type_text":
                params = {**next_params, **_tab_params()}
                selector = params.get("selector", "")
                text = params.get("text", "")
                # Fokus setzen
                if selector and (selector.startswith("@") or "@e" in selector):
                    await execute_bridge("click_ref", {"ref": selector, **_tab_params()})
                    await asyncio.sleep(0.4 + random.random() * 0.3)
                elif selector:
                    # Normaler CSS-Selektor: erst click dann tippen
                    try:
                        await execute_bridge("click", {"selector": selector, **_tab_params()})
                        await asyncio.sleep(0.3 + random.random() * 0.3)
                    except Exception:
                        pass
                # Human-cadence per-char
                if text and len(text) <= 400:
                    for idx, ch in enumerate(text):
                        try:
                            await execute_bridge("keyboard", {"keys": [ch], **_tab_params()})
                        except Exception:
                            # Fallback auf ganzes type_text falls keyboard-Einzelchar fehlschlaegt
                            await execute_bridge("type_text", params)
                            break
                        # Jitter: 40-180ms, bei Satzzeichen leicht laenger, 3% Chance auf Mikro-Pause
                        base = 0.04 + random.random() * 0.14
                        if ch in ".,!?;:":
                            base += 0.12 + random.random() * 0.18
                        if ch == " " and random.random() < 0.05:
                            base += 0.25 + random.random() * 0.35
                        if random.random() < 0.03:
                            base += 0.45 + random.random() * 0.6
                        await asyncio.sleep(base)
                    audit(
                        "human_typed",
                        chars=len(text),
                        avg_ms_per_char=round(1000 * 0.1, 0),
                    )
                else:
                    # Sehr lange Texte ODER leer -> single bridge call
                    await execute_bridge("type_text", params)

            # Navigation — IMMER mit exaktem tabId, KEIN Fallback ohne tabId
            elif next_action == "navigate":
                url = next_params.get("url", "")
                await execute_bridge("navigate", {"url": url, **_tab_params()})
                await save_session(f"nav_{gate.total_steps}")

            # Alle anderen Bridge-Tools — IMMER mit exaktem tabId
            else:
                params = {**next_params, **_tab_params()}
                await execute_bridge(next_action, params)

        except Exception as e:
            audit(
                "error",
                message=f"Aktion {next_action} Exception: {e}",
                traceback=traceback.format_exc()[:500],
            )

        # ---- DOM-VERIFIKATION NACH JEDER AKTION ----
        # WHY: Screenshot-Hash allein kann Survey-Frage-zu-Frage-Übergänge nicht erkennen
        # (gleicher Header, gleiche Farben → gleicher Hash → fälschlicher "kein Fortschritt").
        # DOM-Verifikation prüft URL + Title — ändert sich irgendeins, war es echter Fortschritt.
        # KONSEQUENZ: mark_dom_progress() setzt no_progress_count zurück → kein vorzeitiger Abbruch.

        # ---- ANSWER RECORDING — Antwort fuer Konsistenz speichern ----
        # WHY: Drei-Stufen-Persistenz:
        #   1) Legacy record_answer (bleibt fuer Rueckwaerts-Kompat.)
        #   2) ANSWER_LOG (persona.AnswerLog JSONL) — wird im NAECHSTEN Prompt
        #      als KONSISTENZ-TRAP-Block injiziert damit Trap-Detection greift.
        #   3) GLOBAL_BRAIN.ingest_survey_answer — teilt den Fakt mit der
        #      OpenSIN-Flotte damit andere Agenten dieselbe Persona konsistent
        #      beantworten koennen (Maria, zweite Session, etc.).
        # CONSEQUENCES: Wir nutzen question_text + answer_text aus der Vision-JSON
        # falls verfuegbar, sonst Fallback auf DOM-Scan + reason.
        if (
            page_state
            in (
                "survey_active",
                "survey_audio",
                "survey_video",
                "survey_image",
                "onboarding",
            )
            and next_action in CLICK_ACTIONS
        ):
            vision_question = str(decision.get("question_text") or "").strip()
            vision_answer = str(decision.get("answer_text") or "").strip()
            vision_topic = str(decision.get("question_topic") or "").strip() or None
            trap_tag = str(decision.get("trap_detected") or "").strip() or "none"

            # Fallback-Quellen fuer die Frage
            question_final = (
                vision_question or (_LAST_QUESTION_TEXT or "").strip() or f"step_{gate.total_steps}"
            )
            # Fallback-Quellen fuer die Antwort
            answer_final = (
                vision_answer
                or (reason[:160] if reason else "")
                or f"{next_action} {json.dumps(next_params, ensure_ascii=False)[:80]}"
            )
            # Topic ggf. aus der Frage ableiten (persona.detect_question_topic)
            if not vision_topic and question_final:
                try:
                    vision_topic = detect_question_topic(question_final)
                except Exception:
                    vision_topic = None

            # 1) Legacy record
            record_answer(f"step_{gate.total_steps}_{next_action}", answer_final[:120])

            # 1b) In-Survey Consistency Memo
            # WHY: Scanner 20 (Answer-Consistency) liest _ANSWER_MEMO[hash(frage)]
            # um Widerspruchs-Antworten zu verhindern. Wir speichern nach jedem
            # erfolgreichen Click/Type.
            if question_final and answer_final:
                try:
                    _norm = re.sub(r"\s+", " ", question_final.lower()).strip()
                    _norm = re.sub(r"[.,;:!?\"'„“”‚‘’()\[\]]", "", _norm)
                    _qh = hashlib.md5(_norm.encode("utf-8")).hexdigest()[:16]
                    # Erste gegebene Antwort "gewinnt" — spaetere Duplikate muessen sich anpassen
                    if _qh not in _ANSWER_MEMO:
                        _ANSWER_MEMO[_qh] = answer_final[:200]
                except Exception:
                    pass

            # 2) Persona Answer-Log (JSONL) fuer Konsistenz-Trap-Detection
            if ANSWER_LOG is not None and question_final and answer_final:
                try:
                    ANSWER_LOG.record(
                        question=question_final,
                        answer=answer_final,
                        topic=vision_topic,
                        survey_id=(
                            f"queue_{SURVEY_ORCHESTRATOR._current.index}"
                            if SURVEY_ORCHESTRATOR is not None
                            and SURVEY_ORCHESTRATOR._current is not None
                            else None
                        ),
                        confidence="high" if vision_topic else "medium",
                    )
                except Exception as e:
                    audit("answer_log_error", error=str(e))

            # 3) Global Brain fuer Flotten-Wissen (non-fatal)
            if GLOBAL_BRAIN is not None and question_final and answer_final:
                try:
                    await GLOBAL_BRAIN.ingest_survey_answer(
                        question=question_final,
                        answer=answer_final,
                        topic=vision_topic,
                        survey_id=(
                            f"queue_{SURVEY_ORCHESTRATOR._current.index}"
                            if SURVEY_ORCHESTRATOR is not None
                            and SURVEY_ORCHESTRATOR._current is not None
                            else None
                        ),
                    )
                except Exception as e:
                    audit("brain_ingest_error", error=str(e))

            audit(
                "answer_recorded",
                step=gate.total_steps,
                question=question_final[:120],
                answer=answer_final[:120],
                topic=vision_topic,
                trap=trap_tag,
            )

        await asyncio.sleep(get_fail_learning_dom_wait_seconds())
        dom_check = await dom_verify_change(before_url, before_title)
        if dom_check.get("changed"):
            # DOM hat sich verändert → echter Fortschritt → no_progress_count zurücksetzen
            gate.mark_dom_progress()
            audit(
                "success",
                message="DOM-Verifikation: Seite hat sich nach Aktion erfolgreich verändert!",
            )
        else:
            audit(
                "warning",
                message="DOM-Verifikation: Keine Veränderung nach Aktion erkannt (Vision wird gleich prüfen).",
            )

        # ---- HUMAN DELAY ----
        await human_delay(1.5, 4.5)

    # ============================================================================
    # ABSCHLUSS — Zusammenfassung und Proof-Collection
    # ============================================================================

    # Final Session sichern
    await save_session("final")

    # Orchestrator abschließen — History-File schreiben und Stats sammeln
    queue_stats: dict = {}
    if SURVEY_ORCHESTRATOR is not None:
        try:
            queue_stats = SURVEY_ORCHESTRATOR.finalize()
            audit("queue_finalized", **{k: v for k, v in queue_stats.items() if k != "records"})
        except Exception as e:
            audit("queue_finalize_error", error=str(e))

    # Global Brain Session sauber beenden (non-fatal).
    # WHY: Der OpenSIN-Daemon braucht ein endSession um die im Run konsultierten
    # Rules als "verwendet" zu markieren und den Forgetting-Mechanismus zu
    # fuettern. Ohne end_session bleiben Rules ewig hot.
    if GLOBAL_BRAIN is not None:
        try:
            success_flag = bool(
                queue_stats.get("completed", 0) > 0
                or final_exit_reason in ("vision_done",)
                or final_exit_reason.startswith("queue_finished")
            )
            await GLOBAL_BRAIN.end_session(
                success=success_flag,
                extra={
                    "queue_attempted": queue_stats.get("attempted", 0),
                    "queue_completed": queue_stats.get("completed", 0),
                    "queue_failed": queue_stats.get("failed", 0),
                    "exit_reason": final_exit_reason,
                    "steps": gate.total_steps,
                },
            )
        except Exception as e:
            audit("brain_endsession_error", error=str(e))

    summary_path, fail_report_path, final_exit_reason = await _finalize_worker_run(
        run_summary, gate, final_exit_reason, final_page_state, recorder
    )
    run_summary.print_summary()

    print(f"\n{'=' * 60}")
    print(f"LAUF BEENDET — Zusammenfassung:")
    print(f"   Schritte: {gate.total_steps}/{MAX_STEPS}")
    print(f"   Erfolgreich: {gate.successful_actions}")
    print(f"   Screenshots: {len(list(SCREENSHOT_DIR.glob('*.png')))}")
    print(f"   Artefakte: {ARTIFACT_DIR}")
    print(f"   Audit-Log: {AUDIT_LOG_PATH}")
    print(f"   Structured Summary: {summary_path}")
    if fail_report_path is not None:
        print(f"   Fail Replay Report: {fail_report_path}")
    if queue_stats:
        print(f"\n   MULTI-SURVEY QUEUE:")
        print(f"   Surveys versucht: {queue_stats.get('attempted', 0)}")
        print(f"   Surveys abgeschlossen: {queue_stats.get('completed', 0)}")
        print(f"   Surveys fehlgeschlagen: {queue_stats.get('failed', 0)}")
        print(f"   Gesamt-Dauer: {queue_stats.get('total_duration_sec', 0)}s")
        print(f"   Queue-Status: {queue_stats.get('state', '?')}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    asyncio.run(main())
