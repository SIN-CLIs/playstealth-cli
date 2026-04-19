"""
Platform-Profil-Abstraktion fuer den A2A-Survey-Worker.

WHY (DE): Der Worker wurde urspruenglich hart auf HeyPiggy gebaut (Dashboard-URL,
Login-Selektoren, Reward-Regex, Session-Cookie-Domains). Fuer andere Plattformen
(Prolific, Mechanical Turk, Toluna, Clickworker, LifePoints, Swagbucks, Attapoll
usw.) brauchten wir bisher einen Code-Fork. Dieses Modul bricht die Kopplung auf:
jede Plattform beschreibt sich durch ein `PlatformProfile`-Objekt, das zur
Laufzeit geladen wird. Der Worker bleibt identisch, nur das Profil wechselt.

KONSEQUENZEN:
- Neue Plattform = neues Profil (JSON oder Python-Konstante), kein Worker-Fork.
- Regex fuer EUR-Rewards, Login-URLs, Dashboard-URLs, Session-Domains, erwartete
  Redirect-Hostnames, reserve-Keywords fuer DQ/Quota sind alles Profil-Attribute.
- Tests koennen Mock-Profile laden.

Format (JSON-Schema in profile_json()):
{
  "name": "heypiggy",
  "dashboard_url": "https://www.heypiggy.com/",
  "login_url": "https://www.heypiggy.com/login",
  "session_domains": ["heypiggy.com", "www.heypiggy.com", "puresurveys.com", ...],
  "reward_currency": "EUR",
  "reward_patterns": [
    "(?:gutgeschrieben|erhalten|credit|earned|\\+)\\s*(\\d+[.,]\\d{1,2})\\s*(?:EUR|\u20ac|eur)",
    "(\\d+[.,]\\d{1,2})\\s*(?:EUR|\u20ac|eur)\\s*(?:gutgeschrieben|erhalten|credit|earned)"
  ],
  "dq_phrases": ["disqualif", "nicht teilnehmen", "screening out", "not eligible"],
  "quota_phrases": ["quote erreicht", "quota full", "survey is full"],
  "success_phrases": ["vielen dank", "thank you", "erfolgreich abgeschlossen"],
  "login_email_selectors": ["input[type=email]", "input[name=email]"],
  "login_password_selectors": ["input[type=password]"],
  "login_submit_selectors": ["button[type=submit]"],
  "rating_required": true,
  "rating_min_stars": 3,
  "env_email_key": "HEYPIGGY_EMAIL",
  "env_password_key": "HEYPIGGY_PASSWORD",
  "locale": "de-DE"
}
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PlatformProfile:
    """
    Beschreibt eine Survey-Plattform vollstaendig.

    Alle Attribute haben sinnvolle Defaults damit partielle Profile (z.B. nur
    name + dashboard_url + env_keys) direkt nutzbar sind. Regex-Listen werden
    vorab kompiliert fuer Performance.
    """

    # Identitaet
    name: str
    dashboard_url: str
    login_url: str = ""

    # Session / Cookies
    # WHY: session_store.py muss wissen welche Domains gecacht werden sollen.
    session_domains: tuple[str, ...] = field(default_factory=tuple)

    # Rewards
    reward_currency: str = "EUR"
    reward_patterns: tuple[str, ...] = field(
        default_factory=lambda: (
            r"(?:gutgeschrieben|erhalten|credit|earned|\+)\s*(\d+[.,]\d{1,2})\s*(?:EUR|€|eur)",
            r"(\d+[.,]\d{1,2})\s*(?:EUR|€|eur)\s*(?:gutgeschrieben|erhalten|credit|earned)",
        )
    )

    # Phrase-Bibliotheken fuer Klassifikation
    dq_phrases: tuple[str, ...] = field(
        default_factory=lambda: (
            "disqualif",
            "nicht teilnehmen",
            "screening out",
            "screened out",
            "not eligible",
            "kein passender",
        )
    )
    quota_phrases: tuple[str, ...] = field(
        default_factory=lambda: (
            "quote erreicht",
            "quote voll",
            "quota full",
            "quota reached",
            "survey is full",
            "umfrage ist voll",
            "bereits genug teilnehmer",
        )
    )
    success_phrases: tuple[str, ...] = field(
        default_factory=lambda: (
            "vielen dank",
            "thank you",
            "erfolgreich abgeschlossen",
            "successfully completed",
            "completed",
        )
    )

    # Login-Selektoren (Fallback-Liste — der Worker probiert sie der Reihe nach)
    login_email_selectors: tuple[str, ...] = field(
        default_factory=lambda: (
            "input[type=email]",
            "input[name=email]",
            "input[id=email]",
        )
    )
    login_password_selectors: tuple[str, ...] = field(
        default_factory=lambda: (
            "input[type=password]",
            "input[name=password]",
        )
    )
    login_submit_selectors: tuple[str, ...] = field(
        default_factory=lambda: (
            "button[type=submit]",
            "input[type=submit]",
        )
    )

    # Post-Survey Rating (manche Plattformen verlangen Sterne-Bewertung)
    rating_required: bool = False
    rating_min_stars: int = 3

    # ENV-Vars fuer Credentials (flexibel damit mehrere Plattformen parallel
    # koexistieren koennen: HEYPIGGY_EMAIL vs PROLIFIC_EMAIL)
    env_email_key: str = ""
    env_password_key: str = ""

    # Locale fuer Sprach-Heuristiken (de-DE, en-US, ...)
    locale: str = "de-DE"

    # Flag: Platform nutzt Router (Cint/Lucid/PureSpectrum) — dann bitte
    # panel_overrides.detect_panel() zusaetzlich beachten.
    uses_router_panels: bool = True

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    def compiled_reward_patterns(self) -> list[re.Pattern[str]]:
        """Kompiliert die Reward-Regex einmalig."""
        return [re.compile(p, re.IGNORECASE) for p in self.reward_patterns]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformProfile":
        """
        Baut ein Profil aus dict. Unbekannte Keys werden ignoriert,
        fehlende Pflichtfelder (name, dashboard_url) werfen.
        Listen werden zu Tuples (frozen-dataclass-compat).
        """
        if "name" not in data or "dashboard_url" not in data:
            raise ValueError("PlatformProfile benötigt mindestens 'name' und 'dashboard_url'")
        fields_tuple = {
            "session_domains",
            "reward_patterns",
            "dq_phrases",
            "quota_phrases",
            "success_phrases",
            "login_email_selectors",
            "login_password_selectors",
            "login_submit_selectors",
        }
        clean: dict[str, Any] = {}
        for k, v in data.items():
            if k in fields_tuple and isinstance(v, list):
                clean[k] = tuple(v)
            else:
                clean[k] = v
        # Unbekannte Keys herausfiltern
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in clean.items() if k in allowed}
        return cls(**clean)  # type: ignore[arg-type]

    @classmethod
    def from_json_file(cls, path: Path | str) -> "PlatformProfile":
        """Laedt Profil aus JSON-Datei."""
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------
# WHY: Wir liefern HeyPiggy als Default und ein paar Community-bekannte
# Plattformen als Skelett mit. Wer einen neuen Anbieter erschliesst kann
# ein JSON unter profiles/<name>.json ablegen — das wird via ENV
# PLATFORM_PROFILE=/pfad/profil.json geladen.

HEYPIGGY_PROFILE = PlatformProfile(
    name="heypiggy",
    dashboard_url="https://www.heypiggy.com/",
    login_url="https://www.heypiggy.com/login",
    session_domains=(
        "heypiggy.com",
        "www.heypiggy.com",
        "purespectrum.com",
        "app.purespectrum.com",
        "dynata.com",
        "research.dynata.com",
        "sapiorsrch.com",
        "lucidhq.com",
        "samplicio.us",
        "cint.com",
        "engine.cint.com",
    ),
    rating_required=True,
    rating_min_stars=3,
    env_email_key="HEYPIGGY_EMAIL",
    env_password_key="HEYPIGGY_PASSWORD",
    locale="de-DE",
    uses_router_panels=True,
)

# Skelette fuer andere Plattformen — nur die minimalen Felder, der Rest faellt
# auf sinnvolle Defaults zurueck. Kann live via JSON-Profil erweitert werden.
PROLIFIC_PROFILE = PlatformProfile(
    name="prolific",
    dashboard_url="https://app.prolific.com/",
    login_url="https://app.prolific.com/login",
    session_domains=("prolific.com", "app.prolific.com"),
    reward_currency="GBP",
    reward_patterns=(
        r"(?:£|GBP)\s*(\d+[.,]\d{1,2})",
        r"(\d+[.,]\d{1,2})\s*(?:£|GBP)",
    ),
    env_email_key="PROLIFIC_EMAIL",
    env_password_key="PROLIFIC_PASSWORD",
    locale="en-GB",
    uses_router_panels=False,
)

CLICKWORKER_PROFILE = PlatformProfile(
    name="clickworker",
    dashboard_url="https://workplace.clickworker.com/",
    login_url="https://workplace.clickworker.com/en/login",
    session_domains=("clickworker.com", "workplace.clickworker.com"),
    env_email_key="CLICKWORKER_EMAIL",
    env_password_key="CLICKWORKER_PASSWORD",
    locale="en-US",
    uses_router_panels=False,
)

ATTAPOLL_PROFILE = PlatformProfile(
    name="attapoll",
    dashboard_url="https://attapoll.app/",
    session_domains=("attapoll.app",),
    env_email_key="ATTAPOLL_EMAIL",
    env_password_key="ATTAPOLL_PASSWORD",
    locale="en-US",
    uses_router_panels=True,
)

BUILTIN_PROFILES: dict[str, PlatformProfile] = {
    "heypiggy": HEYPIGGY_PROFILE,
    "prolific": PROLIFIC_PROFILE,
    "clickworker": CLICKWORKER_PROFILE,
    "attapoll": ATTAPOLL_PROFILE,
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def load_active_profile() -> PlatformProfile:
    """
    Ermittelt das aktive Profil in dieser Prioritaet:
      1) ENV PLATFORM_PROFILE_JSON    -> Pfad zu JSON-Datei
      2) ENV PLATFORM_PROFILE         -> Built-in-Name (heypiggy/prolific/...)
      3) Fallback: heypiggy

    WHY: Der Worker-Entry-Point ruft load_active_profile() genau einmal beim
    Start und cached das Ergebnis im Modul-Global. Kein Re-Load pro Step.
    """
    json_path = os.environ.get("PLATFORM_PROFILE_JSON", "").strip()
    if json_path:
        try:
            return PlatformProfile.from_json_file(json_path)
        except Exception as e:
            # Fehlerhaftes JSON darf den Worker nicht blockieren — wir loggen
            # aber fallen auf Built-in zurueck.
            print(f"[platform_profile] JSON-Profil '{json_path}' ungueltig: {e}")

    name = os.environ.get("PLATFORM_PROFILE", "heypiggy").strip().lower()
    if name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[name]

    return HEYPIGGY_PROFILE


# Modul-weit gecached
_ACTIVE: PlatformProfile | None = None


def active() -> PlatformProfile:
    """Gibt das einmal geladene aktive Profil zurueck."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_active_profile()
    return _ACTIVE


def reset_active() -> None:
    """Nur fuer Tests: erzwingt Re-Load beim naechsten active()-Aufruf."""
    global _ACTIVE
    _ACTIVE = None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    for name, p in BUILTIN_PROFILES.items():
        print(f"=== {name} ===")
        print(f"  dashboard: {p.dashboard_url}")
        print(f"  currency:  {p.reward_currency}")
        print(f"  domains:   {len(p.session_domains)}")
        print(f"  env:       {p.env_email_key} / {p.env_password_key}")
        # Reward-Regex testen
        test = f"Ihnen wurden 0,25 {p.reward_currency} gutgeschrieben" if p.reward_currency == "EUR" else "You earned £2.50"
        for rx in p.compiled_reward_patterns():
            m = rx.search(test)
            if m:
                print(f"  reward regex hit: {m.group(0)}")
                break
    # Roundtrip JSON
    j = HEYPIGGY_PROFILE.to_json()
    back = PlatformProfile.from_dict(json.loads(j))
    assert back == HEYPIGGY_PROFILE
    print("\nAlle Profile laden + JSON-Roundtrip ok.")
