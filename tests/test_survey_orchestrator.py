# ================================================================================
# DATEI: test_survey_orchestrator.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""
Tests fuer survey_orchestrator.py — prueft Queue-Management, explizite Listen,
Auto-Detect, Cooldown-Logik und Limit-Handling.

Passt zur echten API in survey_orchestrator.py:
- begin() MUSS vor state-checks laufen
- Records tragen start_time (nicht started_at)
- failed_count / completed_count gibt es ueber stats_summary() und records
- _pop_next_url existiert nicht — die URL-Aufloesung passiert intern in
  _navigate_to_next()
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from survey_orchestrator import QueueState, SurveyOrchestrator


@pytest.fixture
def bridge():
    # -------------------------------------------------------------------------
    # FUNKTION: bridge
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    return AsyncMock()


@pytest.fixture
def tmp_history():
    # -------------------------------------------------------------------------
    # FUNKTION: tmp_history
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "history.json"


@pytest.fixture
def orch(bridge, tmp_history):
    # -------------------------------------------------------------------------
    # FUNKTION: orch
    # PARAMETER: bridge, tmp_history
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    return SurveyOrchestrator(
        execute_bridge=bridge,
        tab_params_factory=lambda: {"tab_id": "t1"},
        dashboard_url="https://www.heypiggy.com/",
        explicit_urls=["https://www.heypiggy.com/survey/1"],
        autodetect=False,
        max_surveys=5,
        cooldown_sec=0.0,
        cooldown_jitter=0.0,
        history_path=tmp_history,
    )


@pytest.mark.asyncio
async def test_begin_with_explicit_url_starts_running(orch, bridge):
    # -------------------------------------------------------------------------
    # FUNKTION: test_begin_with_explicit_url_starts_running
    # PARAMETER: orch, bridge
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    bridge.return_value = {"ok": True}
    record = await orch.begin()
    assert record is not None
    assert orch.state == QueueState.RUNNING
    assert record.start_url == "https://www.heypiggy.com/survey/1"
    assert record.index == 1


@pytest.mark.asyncio
async def test_begin_with_no_urls_and_no_autodetect_sets_exhausted(bridge, tmp_history):
    # -------------------------------------------------------------------------
    # FUNKTION: test_begin_with_no_urls_and_no_autodetect_sets_exhausted
    # PARAMETER: bridge, tmp_history
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    orch = SurveyOrchestrator(
        execute_bridge=bridge,
        tab_params_factory=lambda: {},
        dashboard_url="https://www.heypiggy.com/",
        explicit_urls=[],
        autodetect=False,
        max_surveys=5,
        cooldown_sec=0.0,
        history_path=tmp_history,
    )
    record = await orch.begin()
    assert record is None
    assert orch.state == QueueState.NO_MORE_AVAILABLE


@pytest.mark.asyncio
async def test_explicit_urls_consumed_in_order(bridge, tmp_history):
    # -------------------------------------------------------------------------
    # FUNKTION: test_explicit_urls_consumed_in_order
    # PARAMETER: bridge, tmp_history
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    urls = [
        "https://www.heypiggy.com/survey/1",
        "https://www.heypiggy.com/survey/2",
    ]
    orch = SurveyOrchestrator(
        execute_bridge=bridge,
        tab_params_factory=lambda: {},
        dashboard_url="https://www.heypiggy.com/",
        explicit_urls=urls,
        autodetect=False,
        max_surveys=10,
        cooldown_sec=0.0,
        history_path=tmp_history,
    )
    bridge.return_value = {"ok": True}

    rec1 = await orch.begin()
    assert rec1.start_url == urls[0]

    state = await orch.on_survey_completed(success=True, steps_used=10, end_reason="survey_done")
    assert state == QueueState.RUNNING

    # Die zweite URL muss jetzt current sein
    assert orch.records[-1].start_url == urls[1]


@pytest.mark.asyncio
async def test_max_surveys_limit_triggers_limit_reached(orch, bridge):
    # -------------------------------------------------------------------------
    # FUNKTION: test_max_surveys_limit_triggers_limit_reached
    # PARAMETER: orch, bridge
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Mit max_surveys=5 und 5 abgeschlossenen Surveys muss LIMIT_REACHED kommen."""
    bridge.return_value = {"ok": True}
    await orch.begin()

    # Completed 1 (hat schon einen Record)
    # Simuliere weitere 4 so dass wir bei 5 Completed sind
    for i in range(4):
        await orch.on_survey_completed(success=True, steps_used=10, end_reason="survey_done")

    # Die 5. Completion muss LIMIT_REACHED setzen
    state = await orch.on_survey_completed(success=True, steps_used=10, end_reason="survey_done")
    assert state == QueueState.LIMIT_REACHED
    assert orch.completed_count == 5


@pytest.mark.asyncio
async def test_failed_survey_recorded(orch, bridge):
    # -------------------------------------------------------------------------
    # FUNKTION: test_failed_survey_recorded
    # PARAMETER: orch, bridge
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    bridge.return_value = {"ok": True}
    await orch.begin()
    await orch.on_survey_completed(success=False, steps_used=60, end_reason="max_steps")
    stats = orch.stats_summary()
    assert stats["failed"] == 1
    assert stats["attempted"] >= 1


@pytest.mark.asyncio
async def test_record_has_start_and_end_time(orch, bridge):
    # -------------------------------------------------------------------------
    # FUNKTION: test_record_has_start_and_end_time
    # PARAMETER: orch, bridge
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    bridge.return_value = {"ok": True}
    await orch.begin()
    await orch.on_survey_completed(success=True, steps_used=12, end_reason="survey_done")
    rec = orch.records[0]
    assert rec.start_time > 0
    assert rec.end_time > 0
    assert rec.end_reason == "survey_done"
    assert rec.steps_used == 12


def test_finalize_returns_stats(orch):
    # -------------------------------------------------------------------------
    # FUNKTION: test_finalize_returns_stats
    # PARAMETER: orch
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    stats = orch.finalize()
    assert "attempted" in stats
    assert "completed" in stats
    assert "failed" in stats
    assert "state" in stats
    assert "total_duration_sec" in stats


def test_stats_summary_empty_on_fresh_orchestrator(orch):
    # -------------------------------------------------------------------------
    # FUNKTION: test_stats_summary_empty_on_fresh_orchestrator
    # PARAMETER: orch
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    stats = orch.stats_summary()
    assert stats["attempted"] == 0
    assert stats["completed"] == 0
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_abort_sets_state(orch, bridge):
    # -------------------------------------------------------------------------
    # FUNKTION: test_abort_sets_state
    # PARAMETER: orch, bridge
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    bridge.return_value = {"ok": True}
    await orch.begin()
    orch.abort("test reason")
    assert orch.state == QueueState.ABORTED


@pytest.mark.asyncio
async def test_history_file_written_on_finalize(orch, bridge, tmp_history):
    # -------------------------------------------------------------------------
    # FUNKTION: test_history_file_written_on_finalize
    # PARAMETER: orch, bridge, tmp_history
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    bridge.return_value = {"ok": True}
    await orch.begin()
    await orch.on_survey_completed(success=True, steps_used=5, end_reason="survey_done")
    orch.finalize()
    assert tmp_history.exists()
    content = tmp_history.read_text()
    assert "survey_done" in content


@pytest.mark.asyncio
async def test_v2_dashboard_survey_prefers_explicit_id(monkeypatch, bridge, tmp_history):
    monkeypatch.setenv("OPENSIN_V2", "1")
    bridge.return_value = {
        "items": [
            {
                "selector": "div.survey-item",
                "id": "survey-65467728",
                "text": "Belohnung 2,50 €",
            }
        ]
    }
    orch = SurveyOrchestrator(
        execute_bridge=bridge,
        tab_params_factory=lambda: {},
        dashboard_url="https://www.heypiggy.com/",
        explicit_urls=[],
        autodetect=True,
        max_surveys=5,
        cooldown_sec=0.0,
        cooldown_jitter=0.0,
        history_path=tmp_history,
    )

    best = await orch._find_best_dashboard_survey()

    assert best is not None
    assert best["selector"] == "#survey-65467728"


@pytest.mark.asyncio
async def test_v2_dashboard_survey_normalizes_selector_ref_style(monkeypatch, bridge, tmp_history):
    monkeypatch.setenv("OPENSIN_V2", "1")
    bridge.return_value = {
        "items": [
            {
                "selector": "@e11",
                "text": "Belohnung 2,50 €",
            }
        ]
    }
    orch = SurveyOrchestrator(
        execute_bridge=bridge,
        tab_params_factory=lambda: {},
        dashboard_url="https://www.heypiggy.com/",
        explicit_urls=[],
        autodetect=True,
        max_surveys=5,
        cooldown_sec=0.0,
        cooldown_jitter=0.0,
        history_path=tmp_history,
    )

    best = await orch._find_best_dashboard_survey()

    assert best is not None
    assert best["selector"] == ""
    assert best["ref"] == "e11"
