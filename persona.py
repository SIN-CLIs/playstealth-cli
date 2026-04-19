"""
Persona-Modul — Wahrheits-Backbone fuer den HeyPiggy Survey Worker.

WHY: Der Worker darf NIEMALS luegen oder sich selbst widersprechen. Wenn in
der Umfrage gefragt wird "Wie alt sind Sie?" und im Profil steht "geboren
1990", muss der Agent den korrekten Wert liefern und NIE davon abweichen.
Gleichzeitig werden in Validation-Traps dieselbe Frage oft mehrfach gestellt
(verschiedene Formulierungen) — da MUSS die Antwort konsistent sein, sonst
wird die Umfrage disqualifiziert.

Dieses Modul:
  1. Laedt strukturierte Personen-Profile aus JSON (profiles/<username>.json)
  2. Bietet einen Resolver der zu einer Frage + Antwortoptionen die beste
     Persona-basierte Antwort findet (Demografie, Einkommen, Haushalt, etc.)
  3. Fuehrt ein Konsistenz-Log (answer_history.jsonl) und liefert bei Wieder-
     holungen semantisch aehnlicher Fragen die frueher gegebene Antwort zurueck
  4. Wird in den Vision-Prompt injiziert damit das Vision-LLM die Antwort vor jedem
     Click cross-checken kann.

Design-Prinzip: Keine Halluzination. Fehlt ein Fact im Profil, wird "unknown"
zurueckgegeben — der Worker waehlt dann die plausibelste sichtbare Option
(oder "keine Angabe" wenn verfuegbar) statt irgendwas zu erfinden.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Persona Datenmodell
# ---------------------------------------------------------------------------


@dataclass(frozen=False)
class Persona:
    """
    Vollstaendiges Markt-Forschungs-Profil einer Person.

    WHY: Umfragen fragen typischerweise nach 30-60 dieser Felder in
    verschiedenen Kombinationen. Wir halten alle bekannten Werte strukturiert,
    damit der Resolver schnell matchen kann ohne auf LLM angewiesen zu sein.

    CONSEQUENCES: Felder die "None" / "" / () sind gelten als UNBEKANNT —
    der Worker fallback-t dann auf "plausibelste Option" statt zu luegen.
    """

    # --- Identitaet -------------------------------------------------------
    username: str = ""
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    date_of_birth: str = ""  # ISO: "1990-05-15"
    gender: str = ""  # "male" | "female" | "non_binary" | "prefer_not"
    email: str = ""
    phone: str = ""

    # --- Geografie --------------------------------------------------------
    country: str = ""  # ISO-2: "DE"
    country_name: str = ""  # "Deutschland"
    region: str = ""  # "Nordrhein-Westfalen"
    city: str = ""
    postal_code: str = ""
    language_primary: str = "de"
    languages_spoken: tuple[str, ...] = field(default_factory=tuple)

    # --- Haushalt ---------------------------------------------------------
    marital_status: str = ""  # single|married|partnered|divorced|widowed
    household_size: int = 0
    children_count: int = 0
    children_ages: tuple[int, ...] = field(default_factory=tuple)
    pets: tuple[str, ...] = field(default_factory=tuple)

    # --- Beruf / Bildung --------------------------------------------------
    employment_status: str = ""
    occupation: str = ""
    industry: str = ""
    work_hours_per_week: int = 0
    education_level: str = ""

    # --- Einkommen --------------------------------------------------------
    income_monthly_net_eur: int = 0
    income_monthly_gross_eur: int = 0
    income_yearly_gross_eur: int = 0
    household_income_monthly_eur: int = 0

    # --- Wohnen / Besitz --------------------------------------------------
    housing_type: str = ""  # apartment_rented|apartment_owned|house_rented|house_owned
    rooms_in_dwelling: int = 0
    car_ownership: str = ""  # none|one|multiple
    cars_in_household: int = 0

    # --- Lifestyle --------------------------------------------------------
    smoking: str = ""  # none|occasional|daily
    alcohol_consumption: str = ""  # none|rarely|weekly|daily
    sports_per_week_hours: int = 0

    # --- Konsum -----------------------------------------------------------
    hobbies: tuple[str, ...] = field(default_factory=tuple)
    interests: tuple[str, ...] = field(default_factory=tuple)
    shopping_habits: dict[str, str] = field(default_factory=dict)
    brand_preferences: dict[str, tuple[str, ...]] = field(default_factory=dict)
    social_media_platforms: tuple[str, ...] = field(default_factory=tuple)
    streaming_services: tuple[str, ...] = field(default_factory=tuple)
    news_sources: tuple[str, ...] = field(default_factory=tuple)

    # --- Sensibel (optional, nur wenn explizit gesetzt) ------------------
    political_leaning: str = ""
    religion: str = ""
    sexual_orientation: str = ""
    health_conditions: tuple[str, ...] = field(default_factory=tuple)

    # --- Offene Zusatz-Fakten fuer alles was der schema nicht abdeckt ----
    extra_facts: dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------------
    # Berechnete Felder
    # ---------------------------------------------------------------------

    @property
    def age(self) -> int:
        """
        Berechnetes Alter in vollen Jahren aus date_of_birth.

        WHY: Viele Umfragen fragen nach Alter oder Alters-Bracket. Dieses
        aus dem Geburtsdatum abzuleiten garantiert dass wir in 5 Jahren nicht
        noch immer "34" antworten — der Wert ist immer aktuell.
        """
        if not self.date_of_birth:
            return 0
        try:
            bd = datetime.fromisoformat(self.date_of_birth)
            today = datetime.now()
            years = today.year - bd.year
            if (today.month, today.day) < (bd.month, bd.day):
                years -= 1
            return max(0, years)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


PROFILES_DIR = Path("profiles")


def load_persona(username: str, profiles_dir: Path | None = None) -> Persona | None:
    """
    Laedt ein Persona-Profil aus profiles/<username>.json.

    Returns None wenn das File nicht existiert — der Worker laeuft dann
    ohne Persona-Context weiter (legacy-Modus). Niemals raisen — Persona
    ist optional, der Worker soll nie daran sterben.
    """
    base = profiles_dir or PROFILES_DIR
    path = base / f"{username}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    # tuples werden in JSON als Listen gespeichert — zurueckkonvertieren
    for list_field in (
        "languages_spoken", "children_ages", "pets", "hobbies", "interests",
        "social_media_platforms", "streaming_services", "news_sources",
        "health_conditions",
    ):
        if list_field in raw and isinstance(raw[list_field], list):
            raw[list_field] = tuple(raw[list_field])
    if "brand_preferences" in raw and isinstance(raw["brand_preferences"], dict):
        raw["brand_preferences"] = {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in raw["brand_preferences"].items()
        }
    # unbekannte Keys ignorieren — waer sonst brittle gegen Schema-Evolution
    known = {f for f in Persona.__dataclass_fields__}
    filtered = {k: v for k, v in raw.items() if k in known}
    return Persona(**filtered)


def save_persona(persona: Persona, profiles_dir: Path | None = None) -> Path:
    """Persistiert ein Persona-Profil fuer spaetere Runs."""
    base = profiles_dir or PROFILES_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{persona.username}.json"
    data = asdict(persona)
    # tuples werden zu Listen damit JSON-serialisierbar
    for k, v in list(data.items()):
        if isinstance(v, tuple):
            data[k] = list(v)
        elif isinstance(v, dict):
            data[k] = {
                kk: list(vv) if isinstance(vv, tuple) else vv
                for kk, vv in v.items()
            }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Answer Resolver — Frage -> Persona-basierte Antwort
# ---------------------------------------------------------------------------


# Keyword-Mapping fuer deutsche & englische Survey-Fragen.
# WHY: 90% aller Umfragen nutzen aehnliche Formulierungen. Ein gut gepflegtes
# Mapping erspart dem LLM Rate-Arbeit und macht Antworten deterministisch.
_QUESTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "age": (
        "alter", "wie alt", "wie jung", "geburtsjahr", "geburtsdatum",
        "jahrgang", "age", "how old", "year of birth", "date of birth",
        "jahr wurden sie geboren", "in welchem jahr geboren",
        "when were you born", "was ist ihr alter", "ihr alter",
    ),
    "gender": (
        "geschlecht", "ihr geschlecht", "sind sie maennlich", "gender",
        "are you male", "sex", "weiblich oder maennlich", "maennlich oder weiblich",
    ),
    "country": (
        "in welchem land", "land", "staat wohnen", "country",
        "country of residence", "wohnsitz land",
    ),
    "region": (
        "bundesland", "region", "state", "province", "welches bundesland",
    ),
    "city": (
        "welcher stadt", "in welcher stadt", "wohnort", "city",
        "town", "in which city",
    ),
    "postal_code": (
        "postleitzahl", "plz", "postal code", "zip code", "zip",
    ),
    "marital_status": (
        "familienstand", "verheiratet", "ledig", "marital status",
        "married", "single", "beziehungsstatus",
    ),
    "household_size": (
        "haushalts", "personen im haushalt", "wie viele menschen leben",
        "household size", "how many people live",
    ),
    "children_count": (
        "kinder", "wie viele kinder", "children", "kids", "how many children",
    ),
    "employment_status": (
        "beschaeftigung", "berufstaetig", "berufsstatus", "arbeiten sie derzeit",
        "employment status", "employed", "are you working", "beruflich taetig",
        "sind sie angestellt",
    ),
    "occupation": (
        "beruf", "taetigkeit", "was arbeiten sie", "occupation",
        "profession", "job title", "was machen sie beruflich",
    ),
    "industry": (
        "branche", "industrie", "wirtschaftszweig", "industry", "sector",
        "in welcher branche", "welche branche",
    ),
    "education_level": (
        "bildung", "schulabschluss", "bildungsabschluss", "hoechster abschluss",
        "education level", "highest education", "akademischer grad",
    ),
    "income_monthly_net_eur": (
        "nettoeinkommen monatlich", "monatliches nettoeinkommen",
        "monthly net income",
    ),
    "income_monthly_gross_eur": (
        "bruttoeinkommen monatlich", "monatliches bruttoeinkommen",
        "monthly gross income",
    ),
    "income_yearly_gross_eur": (
        "jahresbruttoeinkommen", "bruttojahreseinkommen", "jaehrliches einkommen",
        "yearly income", "annual income",
    ),
    "household_income_monthly_eur": (
        "haushaltseinkommen", "household income", "familieneinkommen",
    ),
    "housing_type": (
        "wohnsituation", "wohnen sie", "mieten oder eigentum", "miete",
        "eigentum", "housing type", "do you own or rent",
    ),
    "car_ownership": (
        "besitzen sie ein auto", "wie viele autos", "fahrzeug im haushalt",
        "car ownership", "do you own a car",
    ),
    "smoking": (
        "rauchen sie", "rauchen", "smoke", "tabakkonsum", "do you smoke",
    ),
    "alcohol_consumption": (
        "alkohol", "wie oft trinken sie", "alcohol", "drinking habits",
    ),
    "hobbies": (
        "hobbys", "freizeitaktivitaeten", "hobbies", "what do you do for fun",
    ),
    "interests": (
        "interessen", "woran sind sie interessiert", "interests",
        "was interessiert sie",
    ),
    "social_media_platforms": (
        "welche sozialen netzwerke", "social media", "facebook twitter",
        "which platforms do you use",
    ),
    "streaming_services": (
        "streaming", "netflix", "amazon prime", "disney plus", "welche streaming",
    ),
    "news_sources": (
        "nachrichtenquellen", "woher beziehen sie nachrichten", "news sources",
    ),
}


def _normalize(text: str) -> str:
    """Kleinbuchstaben + Umlaute aufloesen fuer robustes Matching."""
    t = text.lower()
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    for k, v in replacements.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


# Topic-Priority-Bonus: spezifischere Felder gewinnen gegen generische.
# WHY: In "In welcher Branche arbeiten Sie?" matcht "arbeiten sie" (employment)
# aber auch "branche" (industry). Semantisch ist die Frage klar ueber die Branche
# — also muss `industry` gewinnen, obwohl sein Keyword kuerzer ist.
_TOPIC_PRIORITY: dict[str, int] = {
    # Spezifische Fakten zuerst
    "postal_code": 20,
    "income_monthly_net_eur": 18,
    "income_monthly_gross_eur": 18,
    "income_yearly_gross_eur": 18,
    "household_income_monthly_eur": 18,
    "industry": 15,
    "occupation": 14,
    "education_level": 13,
    "marital_status": 12,
    "children_count": 12,
    "household_size": 11,
    "housing_type": 11,
    "car_ownership": 11,
    "region": 10,
    "city": 10,
    "country": 9,
    "age": 8,
    "gender": 8,
    "smoking": 7,
    "alcohol_consumption": 7,
    "hobbies": 6,
    "interests": 6,
    "streaming_services": 6,
    "social_media_platforms": 6,
    "news_sources": 6,
    # Generische Fallbacks
    "employment_status": 3,
}


def detect_question_topic(question_text: str) -> str | None:
    """
    Erkennt welches Persona-Feld eine Frage wahrscheinlich meint.

    Returns den Persona-Feldnamen (z.B. "age", "gender") oder None.

    Ranking:
      score = len(kw) + _TOPIC_PRIORITY[topic]
    So gewinnt ein spezifisches Topic (industry=15) gegen ein generisches
    (employment_status=3) auch wenn das generische Keyword laenger ist.
    """
    norm = _normalize(question_text)
    best_topic: str | None = None
    best_score = -1
    for topic, keywords in _QUESTION_KEYWORDS.items():
        priority = _TOPIC_PRIORITY.get(topic, 5)
        for kw in keywords:
            if _normalize(kw) in norm:
                score = len(kw) + priority
                if score > best_score:
                    best_score = score
                    best_topic = topic
    return best_topic


def _fmt_age_for_options(age: int, options: list[str]) -> str:
    """
    Findet die passende Antwortoption fuer ein gegebenes Alter.

    Viele Umfragen nutzen Brackets wie "25-34", "35-44". Wir parsen diese
    und geben die Option zurueck in die das Alter faellt.
    """
    if not options:
        return str(age)
    for opt in options:
        # Bracket wie "25-34" oder "25 bis 34" oder "25 - 34"
        m = re.search(r"(\d{1,3})\s*(?:-|bis|to)\s*(\d{1,3})", opt)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= age <= hi:
                return opt
        # "ueber 65" / "65+" / "ab 65"
        m2 = re.search(r"(?:ueber|ab|over|\+)\s*(\d{1,3})", _normalize(opt))
        if m2 and age >= int(m2.group(1)):
            return opt
        # "unter 18" / "under 18"
        m3 = re.search(r"(?:unter|under|bis zu)\s*(\d{1,3})", _normalize(opt))
        if m3 and age < int(m3.group(1)):
            return opt
        # Exakte Zahl
        if str(age) == _normalize(opt).strip():
            return opt
    return str(age)


def _fmt_gender(gender: str, options: list[str]) -> str:
    """Findet die passende Geschlechts-Option."""
    if not gender or not options:
        return gender
    mapping = {
        "male": ("maennlich", "mann", "male", "m"),
        "female": ("weiblich", "frau", "female", "f"),
        "non_binary": ("divers", "non-binary", "nonbinary", "other"),
        "prefer_not": ("keine angabe", "prefer not", "moechte nicht sagen"),
    }
    want = mapping.get(gender, (gender,))
    for opt in options:
        norm = _normalize(opt)
        for w in want:
            if _normalize(w) in norm:
                return opt
    return gender


def _best_option_match(value: str, options: list[str]) -> str | None:
    """Fuzzy-Match: findet die aehnlichste Option zu einem Klartext-Wert."""
    if not value or not options:
        return None
    v = _normalize(value)
    best: tuple[float, str] = (0.0, "")
    for opt in options:
        ratio = SequenceMatcher(None, v, _normalize(opt)).ratio()
        if v in _normalize(opt) or _normalize(opt) in v:
            ratio = max(ratio, 0.9)
        if ratio > best[0]:
            best = (ratio, opt)
    return best[1] if best[0] >= 0.5 else None


def resolve_answer(
    persona: Persona,
    question_text: str,
    options: list[str] | None = None,
) -> dict[str, Any]:
    """
    Findet die Persona-konforme Antwort auf eine Umfrage-Frage.

    Returns: {
        "topic": <persona-feld oder None>,
        "raw_value": <rohwert aus Persona>,
        "matched_option": <beste option aus der Liste oder None>,
        "confidence": "high" | "medium" | "low" | "unknown",
        "reason": <Menschlich-lesbare Begruendung>,
    }

    WHY returns dict und nicht string: der Worker muss wissen ob die Antwort
    "high confidence Persona-Fact" ist (dann MUSS er sie nehmen) oder nur
    "low confidence Fuzzy-Match" (dann nur als Hinweis im Prompt).
    """
    topic = detect_question_topic(question_text)
    options = options or []

    if topic is None:
        return {
            "topic": None,
            "raw_value": None,
            "matched_option": None,
            "confidence": "unknown",
            "reason": "Frage passt zu keinem Persona-Feld",
        }

    # Sonderbehandlung: Alter wird aus date_of_birth berechnet
    if topic == "age":
        age = persona.age
        if age <= 0:
            return {
                "topic": "age", "raw_value": None, "matched_option": None,
                "confidence": "unknown",
                "reason": "Geburtsdatum im Profil nicht gesetzt",
            }
        matched = _fmt_age_for_options(age, options) if options else str(age)
        return {
            "topic": "age", "raw_value": age, "matched_option": matched,
            "confidence": "high",
            "reason": f"Alter {age} aus Geburtsdatum {persona.date_of_birth} berechnet",
        }

    # Sonderbehandlung: Geschlecht mit Mapping
    if topic == "gender":
        g = persona.gender
        if not g:
            return {
                "topic": "gender", "raw_value": None, "matched_option": None,
                "confidence": "unknown", "reason": "Geschlecht nicht im Profil",
            }
        matched = _fmt_gender(g, options) if options else g
        return {
            "topic": "gender", "raw_value": g, "matched_option": matched,
            "confidence": "high", "reason": f"Geschlecht {g} aus Profil",
        }

    # Einkommen: Bracket-Matching
    if topic.startswith("income_") or topic == "household_income_monthly_eur":
        value = getattr(persona, topic, 0)
        if not value or value <= 0:
            return {
                "topic": topic, "raw_value": None, "matched_option": None,
                "confidence": "unknown",
                "reason": f"{topic} nicht im Profil gesetzt",
            }
        matched = None
        if options:
            for opt in options:
                m = re.search(r"(\d{3,6})\s*(?:-|bis|to|\u2013)\s*(\d{3,6})", opt)
                if m:
                    lo = int(m.group(1))
                    hi = int(m.group(2))
                    if lo <= value <= hi:
                        matched = opt
                        break
        return {
            "topic": topic, "raw_value": value, "matched_option": matched,
            "confidence": "high" if matched else "medium",
            "reason": f"{topic}={value} aus Profil",
        }

    # Standard: direkter Feldzugriff + Fuzzy-Match auf Optionen
    raw = getattr(persona, topic, None)
    if raw is None or raw == "" or raw == 0 or raw == ():
        return {
            "topic": topic, "raw_value": None, "matched_option": None,
            "confidence": "unknown",
            "reason": f"{topic} im Profil nicht gesetzt",
        }
    if isinstance(raw, (list, tuple)):
        # Mehrfach-Werte (hobbies etc.) — liefere alle sichtbaren Matches
        matches = []
        for item in raw:
            m = _best_option_match(str(item), options) if options else None
            if m:
                matches.append(m)
        return {
            "topic": topic, "raw_value": list(raw),
            "matched_option": matches if matches else None,
            "confidence": "high" if matches else "medium",
            "reason": f"{topic}={list(raw)} (multi)",
        }
    matched = _best_option_match(str(raw), options) if options else None
    return {
        "topic": topic, "raw_value": raw, "matched_option": matched,
        "confidence": "high" if matched else "medium",
        "reason": f"{topic}={raw} aus Profil",
    }


# ---------------------------------------------------------------------------
# Konsistenz-Log
# ---------------------------------------------------------------------------


@dataclass
class AnswerLog:
    """
    JSONL-basiertes Konsistenz-Log.

    WHY: Viele Umfragen stellen dieselbe Frage absichtlich 2-3x in unterschied-
    licher Formulierung als "Attention Check". Wenn wir zuerst "34" und dann
    "35" antworten, disqualifizieren wir uns selbst. Dieses Log garantiert
    dass wir semantisch gleiche Fragen IMMER gleich beantworten.
    """

    username: str
    log_path: Path

    def record(
        self,
        question: str,
        answer: str,
        topic: str | None,
        survey_id: str | None = None,
        confidence: str = "medium",
    ) -> None:
        """Haengt einen Eintrag an das JSONL-Log an."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "username": self.username,
            "survey_id": survey_id or "",
            "question": question,
            "answer": answer,
            "topic": topic,
            "confidence": confidence,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def find_prior_answer(
        self,
        question: str,
        similarity_threshold: float = 0.78,
    ) -> dict[str, Any] | None:
        """
        Sucht nach einer semantisch aehnlichen frueheren Frage.

        Zwei Match-Strategien:
          1) Topic-Match: beide Fragen haben dasselbe detect_question_topic()
             (z.B. "Wie alt sind Sie?" und "Bitte geben Sie Ihr Alter an." ->
             beide `age`). Topic-Match schlaegt sofort durch — das ist der
             Validation-Trap-Killer.
          2) Fuzzy-Match: SequenceMatcher-Ratio >= similarity_threshold.

        Returns den gespeicherten Entry oder None.
        """
        if not self.log_path.exists():
            return None
        target = _normalize(question)
        target_topic = detect_question_topic(question)
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        topic_match: dict[str, Any] | None = None
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                stored = _normalize(entry.get("question", ""))
                if not stored:
                    continue
                # 1) Topic-Match (stark): gleiches Persona-Feld -> dieselbe Antwort
                stored_topic = entry.get("topic") or detect_question_topic(
                    entry.get("question", "")
                )
                if (
                    target_topic
                    and stored_topic
                    and target_topic == stored_topic
                    and topic_match is None
                ):
                    topic_match = entry
                # 2) Fuzzy-Match (Fallback)
                ratio = SequenceMatcher(None, target, stored).ratio()
                if ratio > best[0]:
                    best = (ratio, entry)
        if topic_match is not None:
            return topic_match
        if best[0] >= similarity_threshold:
            return best[1]
        return None


# ---------------------------------------------------------------------------
# Prompt-Block-Generator
# ---------------------------------------------------------------------------


def build_persona_prompt_block(
    persona: Persona | None,
    recent_answers: list[dict[str, Any]] | None = None,
) -> str:
    """
    Baut den Persona-Block der in den Vision-Prompt injiziert wird.

    WHY: Das Vision-LLM braucht strukturierten, kompakten Kontext. Zuviel Persona-Data
    frisst Tokens, zu wenig fuehrt zu Halluzinationen. Wir liefern nur Felder
    die gesetzt sind (!= default) und verdichten Tuples/Dicts menschenlesbar.
    """
    if persona is None:
        return ""

    lines = ["===== PERSONA-PROFIL (VERBINDLICH, NIEMALS ABWEICHEN) ====="]
    lines.append(f"Name: {persona.full_name or persona.username}")
    if persona.date_of_birth:
        lines.append(f"Geburtsdatum: {persona.date_of_birth} (Alter: {persona.age})")
    if persona.gender:
        lines.append(f"Geschlecht: {persona.gender}")
    if persona.country_name or persona.country:
        loc_parts = [
            persona.country_name or persona.country,
            persona.region,
            persona.city,
            persona.postal_code,
        ]
        lines.append("Wohnort: " + ", ".join(p for p in loc_parts if p))
    if persona.marital_status:
        lines.append(f"Familienstand: {persona.marital_status}")
    if persona.household_size:
        lines.append(f"Haushaltsgroesse: {persona.household_size}")
    if persona.children_count:
        lines.append(
            f"Kinder: {persona.children_count}"
            + (f" (Alter: {', '.join(str(a) for a in persona.children_ages)})"
               if persona.children_ages else "")
        )
    if persona.employment_status:
        lines.append(f"Beschaeftigung: {persona.employment_status}")
    if persona.occupation:
        lines.append(f"Beruf: {persona.occupation}")
    if persona.industry:
        lines.append(f"Branche: {persona.industry}")
    if persona.education_level:
        lines.append(f"Bildungsabschluss: {persona.education_level}")
    if persona.income_monthly_net_eur:
        lines.append(f"Nettoeinkommen monatlich: {persona.income_monthly_net_eur} EUR")
    if persona.income_yearly_gross_eur:
        lines.append(f"Jahresbruttoeinkommen: {persona.income_yearly_gross_eur} EUR")
    if persona.household_income_monthly_eur:
        lines.append(
            f"Haushaltseinkommen monatlich: {persona.household_income_monthly_eur} EUR"
        )
    if persona.housing_type:
        lines.append(f"Wohnsituation: {persona.housing_type}")
    if persona.car_ownership or persona.cars_in_household:
        lines.append(
            f"Auto: {persona.car_ownership or '?'} ({persona.cars_in_household} im Haushalt)"
        )
    if persona.hobbies:
        lines.append(f"Hobbys: {', '.join(persona.hobbies)}")
    if persona.interests:
        lines.append(f"Interessen: {', '.join(persona.interests)}")
    if persona.social_media_platforms:
        lines.append(f"Soziale Netzwerke: {', '.join(persona.social_media_platforms)}")
    if persona.streaming_services:
        lines.append(f"Streaming: {', '.join(persona.streaming_services)}")
    if persona.brand_preferences:
        for cat, brands in persona.brand_preferences.items():
            lines.append(f"Marken {cat}: {', '.join(brands)}")
    if persona.extra_facts:
        for k, v in persona.extra_facts.items():
            lines.append(f"{k}: {v}")

    lines.append("")
    lines.append("===== WAHRHEITS-REGELN (HARTE PFLICHT) =====")
    lines.append("1. Nutze IMMER die Persona-Werte oben wenn die Frage dazu passt.")
    lines.append("2. Wenn ein Fakt im Profil steht, gib NIE einen anderen Wert an.")
    lines.append("3. Wenn ein Fakt FEHLT: waehle die plausibelste Option — NIEMALS etwas erfinden.")
    lines.append("4. Antworten mueller konsistent sein — dieselbe Frage immer gleich beantworten.")
    lines.append("5. 'Keine Angabe' / 'prefer not to say' nur als allerletzten Ausweg.")

    if recent_answers:
        lines.append("")
        lines.append("===== BEREITS GEGEBENE ANTWORTEN (KONSISTENZ!) =====")
        for entry in recent_answers[-8:]:
            q = entry.get("question", "")[:80]
            a = entry.get("answer", "")[:60]
            lines.append(f"- '{q}' -> '{a}'")

    return "\n".join(lines)
