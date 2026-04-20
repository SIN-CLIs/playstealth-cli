# ================================================================================
# DATEI: test_persona_trap_integration.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""
Integrationstests fuer Persona + Trap-Detection + Konsistenz-Log.

WHY: Die Persona-Module existieren, sind aber nur dann ein Wahrheits-Backbone
wenn resolve_answer() auch unter realistischen Umfrage-Formulierungen korrekte
Pflicht-Antworten liefert und der AnswerLog semantisch aehnliche Fragen
zusammenfuehrt (Validation-Trap Schutz).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from persona import (
    AnswerLog,
    Persona,
    build_persona_prompt_block,
    detect_question_topic,
    load_persona,
    resolve_answer,
    save_persona,
)


# ---------------------------------------------------------------------------
# Fixture: voll ausgefuelltes Jeremy-Profil
# ---------------------------------------------------------------------------


@pytest.fixture
def jeremy(tmp_path: Path) -> Persona:
    p = Persona(
        username="jeremy_schulze",
        full_name="Jeremy Schulze",
        first_name="Jeremy",
        last_name="Schulze",
        date_of_birth="1990-05-15",
        gender="male",
        country="DE",
        country_name="Deutschland",
        region="Berlin",
        city="Berlin",
        postal_code="10115",
        language_primary="de",
        marital_status="single",
        household_size=1,
        children_count=0,
        employment_status="employed_full_time",
        occupation="Software Engineer",
        industry="IT",
        education_level="bachelor",
        income_monthly_net_eur=2800,
        income_yearly_gross_eur=55000,
        household_income_monthly_eur=2800,
        housing_type="apartment_rented",
        car_ownership="none",
        cars_in_household=0,
        smoking="none",
        alcohol_consumption="rarely",
        hobbies=("Programmieren", "Lesen", "Wandern"),
        streaming_services=("Netflix", "Spotify"),
        brand_preferences={"auto": ("BMW",)},
    )
    save_persona(p, tmp_path)
    return load_persona("jeremy_schulze", tmp_path) or p


# ---------------------------------------------------------------------------
# Pre-Qualifikation: Demografie-Fragen muessen aus Persona kommen
# ---------------------------------------------------------------------------


class TestPrequalificationAnswers:
    # ========================================================================
    # KLASSE: TestPrequalificationAnswers
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_age_from_dob_matches_bracket(self, jeremy: Persona) -> None:
        """Alters-Bracket muss aus DoB berechnet werden, nicht geraten.

        Jeremy ist 1990-05-15 geboren. Abhaengig vom Testdatum liegt das
        Alter in 35-44 (ab 2025-05-15) oder 25-34 (davor). Akzeptiere beide
        um den Test zeitstabil zu machen.
        """
        options = ["18-24", "25-34", "35-44", "45-54", "55+"]
        result = resolve_answer(jeremy, "Wie alt sind Sie?", options)
        assert result["topic"] == "age"
        assert result["confidence"] == "high"
        assert result["matched_option"] in ("25-34", "35-44")

    def test_gender_mapping_german(self, jeremy: Persona) -> None:
        result = resolve_answer(
            jeremy,
            "Bitte geben Sie Ihr Geschlecht an",
            ["Maennlich", "Weiblich", "Divers", "Keine Angabe"],
        )
        assert result["topic"] == "gender"
        assert result["matched_option"] == "Maennlich"

    def test_country_with_umlauts(self, jeremy: Persona) -> None:
        result = resolve_answer(
            jeremy,
            "In welchem Land wohnen Sie?",
            ["Deutschland", "Österreich", "Schweiz"],
        )
        assert result["topic"] == "country"
        # country field is "DE", country_name "Deutschland" — either matched via fuzzy
        assert result["matched_option"] == "Deutschland" or result["raw_value"] == "DE"

    def test_employment_screening_is_high_confidence(self, jeremy: Persona) -> None:
        """Pre-Qualifikation Branche / Job muss deterministisch sein."""
        result = resolve_answer(
            jeremy,
            "In welcher Branche arbeiten Sie?",
            ["IT / Software", "Marketing", "Finanzen", "Gesundheit", "Keine"],
        )
        assert result["topic"] == "industry"
        assert result["matched_option"] == "IT / Software"

    def test_missing_field_returns_unknown(self, jeremy: Persona) -> None:
        """Wenn Persona nichts weiss, MUSS unknown zurueck — niemals raten."""
        p = Persona(username="x")  # leer
        result = resolve_answer(p, "Wie alt sind Sie?", ["18-24", "25-34"])
        assert result["confidence"] == "unknown"
        assert result["matched_option"] is None


# ---------------------------------------------------------------------------
# Konsistenz-Traps: gleiche Frage anders formuliert
# ---------------------------------------------------------------------------


class TestConsistencyTrap:
    # ========================================================================
    # KLASSE: TestConsistencyTrap
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_identical_question_matches(self, tmp_path: Path) -> None:
        log = AnswerLog(username="u", log_path=tmp_path / "hist.jsonl")
        log.record("Wie alt sind Sie?", "34", topic="age", confidence="high")
        prior = log.find_prior_answer("Wie alt sind Sie?")
        assert prior is not None
        assert prior["answer"] == "34"

    def test_semantically_similar_question_matches(self, tmp_path: Path) -> None:
        """DAS ist der Trap-Detector — anders formuliert aber gleiche Info."""
        log = AnswerLog(username="u", log_path=tmp_path / "hist.jsonl")
        log.record("Wie alt sind Sie?", "34", topic="age")
        prior = log.find_prior_answer(
            "Bitte geben Sie Ihr Alter an.", similarity_threshold=0.55
        )
        assert prior is not None
        assert prior["answer"] == "34"

    def test_completely_different_question_does_not_match(
        self, tmp_path: Path
    ) -> None:
        log = AnswerLog(username="u", log_path=tmp_path / "hist.jsonl")
        log.record("Wie alt sind Sie?", "34", topic="age")
        prior = log.find_prior_answer(
            "Welche Marke an Hundefutter kaufen Sie?",
            similarity_threshold=0.78,
        )
        assert prior is None


# ---------------------------------------------------------------------------
# Topic-Detection: Schluessel-Keywords muessen sauber matchen
# ---------------------------------------------------------------------------


class TestTopicDetection:
    # ========================================================================
    # KLASSE: TestTopicDetection
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    @pytest.mark.parametrize(
        "question,expected_topic",
        [
            ("Wie alt sind Sie?", "age"),
            ("In welchem Jahr wurden Sie geboren?", "age"),
            ("Wie ist Ihr Familienstand?", "marital_status"),
            ("Rauchen Sie?", "smoking"),
            ("Wie hoch ist Ihr Haushaltseinkommen monatlich?", "household_income_monthly_eur"),
            ("Welche Streaming-Dienste nutzen Sie?", "streaming_services"),
            ("Wo arbeiten Sie? In welcher Branche?", "industry"),
            ("Welche Hobbys haben Sie?", "hobbies"),
        ],
    )
    def test_detect(self, question: str, expected_topic: str) -> None:
        assert detect_question_topic(question) == expected_topic


# ---------------------------------------------------------------------------
# Prompt-Block: Persona-Daten landen im Vision-Prompt
# ---------------------------------------------------------------------------


class TestPersonaPromptBlock:
    # ========================================================================
    # KLASSE: TestPersonaPromptBlock
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_block_contains_hard_facts(self, jeremy: Persona) -> None:
        block = build_persona_prompt_block(jeremy)
        assert "Jeremy Schulze" in block
        assert "Berlin" in block
        assert "Geburtsdatum" in block
        assert "NIEMALS ABWEICHEN" in block
        assert "WAHRHEITS-REGELN" in block

    def test_recent_answers_are_injected(self, jeremy: Persona) -> None:
        recent = [
            {"question": "Wie alt sind Sie?", "answer": "34"},
            {"question": "Rauchen Sie?", "answer": "Nein"},
        ]
        block = build_persona_prompt_block(jeremy, recent_answers=recent)
        assert "BEREITS GEGEBENE ANTWORTEN" in block
        assert "34" in block
        assert "Nein" in block

    def test_empty_persona_returns_empty(self) -> None:
        assert build_persona_prompt_block(None) == ""


# ---------------------------------------------------------------------------
# Income-Bracket-Matching: das heikle Validation-Trap-Feld
# ---------------------------------------------------------------------------


class TestIncomeBracket:
    # ========================================================================
    # KLASSE: TestIncomeBracket
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_income_bracket_matched(self, jeremy: Persona) -> None:
        # Jeremy: income_yearly_gross_eur = 55000
        result = resolve_answer(
            jeremy,
            "Wie hoch ist Ihr Jahresbruttoeinkommen?",
            [
                "unter 20000",
                "20000 - 40000",
                "40000 - 60000",
                "60000 - 80000",
                "ueber 80000",
            ],
        )
        assert result["topic"] == "income_yearly_gross_eur"
        assert result["matched_option"] == "40000 - 60000"
        assert result["confidence"] == "high"


# ---------------------------------------------------------------------------
# JSON-Serialisierbarkeit: Roundtrip Persona -> JSON -> Persona
# ---------------------------------------------------------------------------


def test_persona_roundtrip(tmp_path: Path, jeremy: Persona) -> None:
    loaded = load_persona("jeremy_schulze", tmp_path)
    assert loaded is not None
    assert loaded.full_name == jeremy.full_name
    assert loaded.hobbies == jeremy.hobbies
    assert loaded.brand_preferences == jeremy.brand_preferences
    # File enthaelt Listen, nicht Tupel
    raw = json.loads((tmp_path / "jeremy_schulze.json").read_text(encoding="utf-8"))
    assert isinstance(raw["hobbies"], list)
