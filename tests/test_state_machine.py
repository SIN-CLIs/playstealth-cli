# ================================================================================
# DATEI: test_state_machine.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""
Unit tests for the PageStateMachine.
"""

import pytest
from state_machine import PageState, StateMachine, PageStateMachine


def test_initial_state():
    sm = StateMachine(initial_state=PageState.UNKNOWN)
    assert sm.state == PageState.UNKNOWN


def test_string_based_transition():
    # -------------------------------------------------------------------------
    # FUNKTION: test_string_based_transition
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    sm = StateMachine(initial_state=PageState.DASHBOARD)
    sm.transition("login")
    assert sm.state == PageState.LOGIN
    sm.transition("survey_active")
    assert sm.state == PageState.SURVEY_ACTIVE


def test_unknown_state_fallback():
    # -------------------------------------------------------------------------
    # FUNKTION: test_unknown_state_fallback
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    sm = StateMachine(initial_state=PageState.DASHBOARD)
    sm.transition("nonexistent")
    assert sm.state == PageState.UNKNOWN


def test_global_instance():
    # -------------------------------------------------------------------------
    # FUNKTION: test_global_instance
    # PARAMETER: keine
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    from state_machine import page_state_machine

    # Should be a singleton-like instance
    initial = page_state_machine.state
    page_state_machine.transition("error")
    assert page_state_machine.state == PageState.ERROR
    page_state_machine.set_state(initial)
    assert page_state_machine.state == initial
