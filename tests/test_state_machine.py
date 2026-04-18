"""
Unit tests for the PageStateMachine.
"""

import pytest
from state_machine import PageState, StateMachine, PageStateMachine


def test_initial_state():
    sm = StateMachine(initial_state=PageState.UNKNOWN)
    assert sm.state == PageState.UNKNOWN


def test_string_based_transition():
    sm = StateMachine(initial_state=PageState.DASHBOARD)
    sm.transition("login")
    assert sm.state == PageState.LOGIN
    sm.transition("survey_active")
    assert sm.state == PageState.SURVEY_ACTIVE


def test_unknown_state_fallback():
    sm = StateMachine(initial_state=PageState.DASHBOARD)
    sm.transition("nonexistent")
    assert sm.state == PageState.UNKNOWN


def test_global_instance():
    from state_machine import page_state_machine

    # Should be a singleton-like instance
    initial = page_state_machine.state
    page_state_machine.transition("error")
    assert page_state_machine.state == PageState.ERROR
    page_state_machine.set_state(initial)
    assert page_state_machine.state == initial
