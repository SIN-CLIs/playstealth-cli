#!/usr/bin/env python3
# ================================================================================
# DATEI: panel_overrides.py
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
Panel Overrides — Hardcodierte Screener-Muster pro Panel-Provider
================================================================================
WHY: HeyPiggy vermittelt Umfragen ueber verschiedene Panel-Router
     (PureSpectrum, Dynata, Sapio, Cint, Lucid). Jeder Router hat:
       - Eigene URL-Fingerprints (domain patterns)
       - Eigene Pre-Screener-Flows (Quality-Checks, Red-Herring-Fragen)
       - Eigene Spinner / Redirect-Loops die man erkennen muss
       - Eigene DQ-Signale ("Leider passen Sie nicht..." vs "We're sorry...")

     Ohne spezifisches Wissen muss Vision jedes Mal neu "raten" wo es ist.
     Mit Override-Layer kriegt Vision einen prompten Cheatsheet:
       "Du bist auf PureSpectrum. Ignoriere den X-Spinner, die 'Device-Check'-Seite
        klickst du mit 'Continue', das 'Red-Herring' erkennst du an 'Select blue'."

CONSEQUENCES:
  - detect_panel(url, body_text) matcht in Millisekunden gegen alle Provider.
  - get_panel_hints(panel) liefert einen Prompt-Block mit provider-spezifischen
    Regeln die dom_prescan ans Vision-LLM uebergibt.
  - Neue Panels koennen ohne Code-Aenderung im Worker ergaenzt werden — nur
    ein neuer Eintrag im PANELS-Dict unten.
================================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Pattern


@dataclass(frozen=True)
class PanelRules:
    """Regeln fuer einen spezifischen Panel-Provider."""

    name: str
    url_patterns: tuple[str, ...]
    text_patterns: tuple[str, ...] = ()
    # Provider-spezifische DQ-Signale (case-insensitive substring matches)
    dq_markers: tuple[str, ...] = ()
    # Red-Herring / Quality-Check-Fragen die der Agent EXAKT richtig beantworten MUSS
    quality_traps: tuple[str, ...] = ()
    # Spezifische Weiter-Button-Labels
    continue_labels: tuple[str, ...] = ()
    # Zeit-Trap: Minimalzeit pro Frage in Sekunden — darunter wird geflaggt
    min_seconds_per_question: float = 2.5
    # Freitext-Antworten-Minimum (manche Panels verlangen >20 Zeichen bei Kommentaren)
    min_free_text_chars: int = 12
    # Zusaetzliche Hinweise als Liste
    extra_hints: tuple[str, ...] = ()


# ----------------------------------------------------------------------------
# PANEL-REGISTRY
# ----------------------------------------------------------------------------


PANELS: tuple[PanelRules, ...] = (
    PanelRules(
        name="PureSpectrum",
        url_patterns=(
            "purespectrum.io",
            "pspmarket.com",
            "ps-route.com",
            "pssurveys.io",
        ),
        text_patterns=(
            "pure spectrum",
            "purespectrum",
        ),
        dq_markers=(
            "we're sorry",
            "you did not qualify",
            "we aren't able to offer you",
            "this survey has closed",
            "quota full",
            "leider passen sie nicht",
            "sie gehoeren nicht zur zielgruppe",
            "thank you for your interest",
        ),
        quality_traps=(
            "please select",  # "Please select blue" / "Please select the number 3"
            "attention check",
            "if you are reading this",
            "to show you are paying attention",
            "waehlen sie bitte",
            "aufmerksamkeitstest",
        ),
        continue_labels=(
            "continue",
            "next",
            "weiter",
            "submit",
        ),
        min_seconds_per_question=3.0,
        min_free_text_chars=15,
        extra_hints=(
            "PureSpectrum zeigt haeufig eine 'Device Verification'-Seite mit "
            "einem Ladekreis von 5-15 Sekunden. NICHT klicken, NICHT reloaden — "
            "warten bis sie von selbst weitergeht.",
            "Bei Attention-Check-Fragen ('Please select blue', 'Choose number 3') "
            "IMMER die woertliche Anweisung befolgen — NICHT Persona-Logik. "
            "Falsche Antwort = sofortige Disqualifikation.",
            "Freitext-Kommentare brauchen mindestens 15 Zeichen sonst wird die "
            "Eingabe nicht akzeptiert.",
        ),
    ),
    PanelRules(
        name="Dynata",
        url_patterns=(
            "dynata.com",
            "researchnow.com",
            "samplicio.us",
            "surveyhealthcare.com",
            "opinionsurveypanel.com",
        ),
        text_patterns=(
            "dynata",
            "research now",
            "ssi survey",
        ),
        dq_markers=(
            "we apologize",
            "you do not qualify",
            "survey is full",
            "quota has been reached",
            "thanks for participating",
            "leider keine teilnahme moeglich",
            "sie erfuellen nicht die kriterien",
        ),
        quality_traps=(
            "straight-lining",
            "speeder",
            "please rate the following",  # Typischer Grid-Trap
            "for quality purposes",
            "data quality",
        ),
        continue_labels=(
            "continue",
            "next",
            "weiter",
            "fortfahren",
        ),
        min_seconds_per_question=3.5,  # Dynata hat harte Speeder-Checks
        min_free_text_chars=20,
        extra_hints=(
            "Dynata detektiert 'Straight-Lining' (in Matrizen immer die gleiche "
            "Spalte). Variiere Antworten in Raster-Fragen bewusst um +/- 1 Position.",
            "Dynata-Speeder-Schwelle: unter 3.5 Sekunden pro Frage wird markiert. "
            "Bei scheinbar trivialen Ja/Nein-Fragen immer bewusst langsamer antworten.",
            "Freitext-Minimum: 20 Zeichen. Einwortige Antworten = automatische DQ.",
            "Dynata leitet manchmal ueber einen 'Quota-Check' mit Spinner um. "
            "Der Spinner loest automatisch aus, nichts klicken bis Fragen erscheinen.",
        ),
    ),
    PanelRules(
        name="Sapio",
        url_patterns=(
            "sapioresearch.com",
            "sapio-research.com",
            "sapio-survey.com",
        ),
        text_patterns=(
            "sapio research",
            "sapio survey",
        ),
        dq_markers=(
            "unfortunately you do not qualify",
            "survey quota filled",
            "no further participation",
            "leider sind sie nicht qualifiziert",
        ),
        quality_traps=(
            "red herring",
            "trap question",
            "consistency check",
            "please ignore this",
        ),
        continue_labels=(
            "continue",
            "next",
            "submit",
            "weiter",
        ),
        min_seconds_per_question=3.0,
        min_free_text_chars=12,
        extra_hints=(
            "Sapio nutzt Consistency-Checks: die gleiche Frage wird in Runde 1 und "
            "Runde 3 nochmal gestellt, oft in anderen Worten. Antworte KONSISTENT "
            "(Persona-Profil strikt befolgen, nicht variieren).",
            "Sapio hat eine 'Brand Awareness'-Sektion mit erfundenen Marken als "
            "Trap-Items — wenn nicht sicher, 'Nie gehoert' waehlen.",
        ),
    ),
    PanelRules(
        name="Cint",
        url_patterns=(
            "cint.com",
            "cint-survey.com",
            "lifepointspanel.com",
            "p.cint.link",
        ),
        text_patterns=(
            "cint",
            "lifepoints",
        ),
        dq_markers=(
            "we couldn't find a survey",
            "we're unable to complete",
            "better luck next time",
            "aktuell keine passende",
        ),
        quality_traps=(
            "please select all that apply",  # Bei Cint oft ein Test: mind. 1 waehlen
            "select the option",
        ),
        continue_labels=(
            "continue",
            "next",
            "submit",
        ),
        min_seconds_per_question=2.5,
        min_free_text_chars=10,
        extra_hints=(
            "Cint leitet Router-basiert um: man sieht im ersten Moment oft eine "
            "leere Seite mit 'Redirecting...'. Das ist normal, 3-8 Sekunden warten.",
            "Bei Cint-Multi-Select niemals 'Keine der genannten' zusammen mit "
            "anderen Optionen waehlen — loest Validator-Fehler aus.",
        ),
    ),
    PanelRules(
        name="Lucid",
        url_patterns=(
            "lucidhq.com",
            "lucidholdings.com",
            "samplicio.us",  # Gehoert zu Lucid
            "fulcrum.rpanel.io",
        ),
        text_patterns=(
            "lucid marketplace",
            "fulcrum",
        ),
        dq_markers=(
            "we couldn't match you",
            "survey has been closed",
            "please try again later",
        ),
        quality_traps=(
            "for quality control",
            "please answer honestly",
        ),
        continue_labels=(
            "continue",
            "next",
            "weiter",
        ),
        min_seconds_per_question=3.0,
        min_free_text_chars=15,
        extra_hints=(
            "Lucid Fulcrum Router nutzt mehrfache Redirects. Warte bis die URL "
            "stabil bleibt bevor du klickst.",
        ),
    ),
    PanelRules(
        name="HeyPiggy",
        url_patterns=(
            "heypiggy.com",
        ),
        text_patterns=(
            "heypiggy",
            "deine verfuegbaren erhebungen",
        ),
        dq_markers=(
            "diese umfrage ist leider nicht mehr verfuegbar",
            "du wurdest leider nicht qualifiziert",
            "umfrage abgebrochen",
        ),
        quality_traps=(),
        continue_labels=(
            "weiter",
            "start",
            "teilnehmen",
        ),
        min_seconds_per_question=2.0,
        min_free_text_chars=10,
        extra_hints=(
            "HeyPiggy-Dashboard: klicke IMMER die Kachel mit dem hoechsten "
            "EUR/Minuten-Wert zuerst (siehe DASHBOARD-RANKING-Block).",
            "Nach erfolgreichem Abschluss erscheint ein 5-Sterne-Bewertungs-Dialog. "
            "IMMER 5 Sterne vergeben — hoehere Bewertung = bessere Zuweisungen.",
        ),
    ),
)


# Vorkompilierte Regex-Patterns fuer schnelles Matching
_URL_PATTERNS: dict[str, Pattern[str]] = {
    p.name: re.compile(
        "|".join(re.escape(u) for u in p.url_patterns),
        re.IGNORECASE,
    )
    for p in PANELS
}

_TEXT_PATTERNS: dict[str, Pattern[str]] = {
    p.name: re.compile(
        "|".join(re.escape(t) for t in p.text_patterns),
        re.IGNORECASE,
    )
    for p in PANELS
    if p.text_patterns
}


# ----------------------------------------------------------------------------
# DETECTION
# ----------------------------------------------------------------------------


def detect_panel(
    url: str = "",
    body_text: str = "",
) -> PanelRules | None:
    """
    Erkennt welcher Panel-Provider gerade aktiv ist.
    Priorisiert URL-Match (schneller + praeziser) vor Text-Match.

    Returns: Die passenden PanelRules oder None wenn nichts erkannt.
    """
    url = url or ""
    body_text = body_text or ""

    # 1) URL-Match (strongest signal)
    for panel in PANELS:
        pat = _URL_PATTERNS.get(panel.name)
        if pat and pat.search(url):
            return panel

    # 2) Body-Text-Match (nur wenn URL-Match fehlschlug)
    if body_text:
        snippet = body_text[:2000].lower()
        for panel in PANELS:
            pat = _TEXT_PATTERNS.get(panel.name)
            if pat and pat.search(snippet):
                return panel

    return None


def detect_quality_trap(panel: PanelRules | None, body_text: str) -> str | None:
    """
    Sucht nach einer Quality-Check-/Attention-Frage im Seitentext.
    Returns: der matchende Trap-Marker oder None.
    """
    if not panel or not panel.quality_traps or not body_text:
        return None
    low = body_text.lower()
    for marker in panel.quality_traps:
        if marker.lower() in low:
            return marker
    return None


def detect_panel_dq(panel: PanelRules | None, body_text: str) -> str | None:
    """
    Prueft ob der Seitentext ein provider-spezifisches DQ-Signal enthaelt.
    Returns: der matchende DQ-Marker oder None.
    """
    if not panel or not panel.dq_markers or not body_text:
        return None
    low = body_text.lower()
    for marker in panel.dq_markers:
        if marker.lower() in low:
            return marker
    return None


# ----------------------------------------------------------------------------
# PROMPT-BLOCK GENERATOR
# ----------------------------------------------------------------------------


def build_panel_prompt_block(
    panel: PanelRules | None,
    body_text: str = "",
) -> str:
    """
    Baut einen kompakten Prompt-Block den dom_prescan ans Vision-LLM uebergibt.
    Enthaelt:
      - Name des aktiven Panels
      - Provider-spezifische Quality-Traps (erkannt im aktuellen Body)
      - DQ-Marker (erkannt im aktuellen Body)
      - Hard rules (continue labels, min seconds/chars)
      - Extra hints
    """
    if panel is None:
        return ""

    lines: list[str] = [
        f"===== PANEL ERKANNT: {panel.name} =====",
    ]

    # Quality-Trap-Alarm (hoechste Prioritaet wenn getroffen)
    trap = detect_quality_trap(panel, body_text)
    if trap:
        lines.append(
            f"ATTENTION-CHECK AKTIV: '{trap}' — WOERTLICHE Anweisung befolgen. "
            "NICHT Persona-Logik. Falsche Antwort = DQ."
        )

    # DQ-Marker
    dq = detect_panel_dq(panel, body_text)
    if dq:
        lines.append(
            f"PANEL-DQ-SIGNAL erkannt: '{dq}' — Umfrage als disqualifiziert "
            "markieren und zurueck zum Dashboard."
        )

    # Harte Regeln
    lines.append(
        f"Regeln: min {panel.min_seconds_per_question:.1f}s pro Frage, "
        f"Freitext min {panel.min_free_text_chars} Zeichen, "
        f"Weiter-Labels: {', '.join(panel.continue_labels)}."
    )

    # Provider-spezifische Hinweise
    if panel.extra_hints:
        lines.append("Provider-Hinweise:")
        for hint in panel.extra_hints:
            lines.append(f"  - {hint}")

    return "\n".join(lines)


# ----------------------------------------------------------------------------
# SELF-TEST / DEBUG
# ----------------------------------------------------------------------------


if __name__ == "__main__":
    import sys

    test_cases = [
        ("https://s.purespectrum.io/abc?x=1", ""),
        ("https://panel.dynata.com/survey", ""),
        ("", "Thank you for participating in our Sapio Research survey"),
        ("https://unknown.com/foo", "Lucid Marketplace"),
        ("https://nothing.example", "Completely unrelated content"),
    ]
    for url, body in test_cases:
        p = detect_panel(url, body)
        print(f"URL={url!r:60s} BODY={body[:40]!r:45s} -> {p.name if p else 'None'}")
    if len(sys.argv) > 1:
        p = detect_panel(sys.argv[1])
        if p:
            print("\n" + build_panel_prompt_block(p))
