import pytest

from opensin_runtime import IllegalTransition, RuntimeState, StateMachine


def test_happy_path():
    sm = StateMachine()
    sm.transition(RuntimeState.PLANNING)
    sm.transition(RuntimeState.ACTING)
    sm.transition(RuntimeState.OBSERVING)
    sm.transition(RuntimeState.DONE)
    assert sm.is_terminal
    assert [t.dst for t in sm.history] == [
        RuntimeState.PLANNING,
        RuntimeState.ACTING,
        RuntimeState.OBSERVING,
        RuntimeState.DONE,
    ]


def test_blocks_illegal_transition():
    sm = StateMachine()
    with pytest.raises(IllegalTransition):
        sm.transition(RuntimeState.ACTING)  # IDLE cannot jump straight to ACTING


def test_blocks_after_terminal():
    sm = StateMachine()
    sm.transition(RuntimeState.PLANNING)
    sm.transition(RuntimeState.FAILED)
    with pytest.raises(IllegalTransition):
        sm.transition(RuntimeState.PLANNING)


def test_challenge_then_recover():
    sm = StateMachine()
    sm.transition(RuntimeState.PLANNING)
    sm.transition(RuntimeState.ACTING)
    sm.transition(RuntimeState.CHALLENGED, reason="cf")
    sm.transition(RuntimeState.RECOVERING)
    sm.transition(RuntimeState.PLANNING)
    assert sm.state == RuntimeState.PLANNING


def test_on_transition_callback():
    seen = []
    sm = StateMachine(on_transition=seen.append)
    sm.transition(RuntimeState.PLANNING, reason="start")
    assert seen and seen[0].reason == "start"


def test_snapshot_shape():
    sm = StateMachine()
    sm.transition(RuntimeState.PLANNING)
    snap = sm.snapshot()
    assert snap["state"] == "PLANNING"
    assert snap["seq"] == 1
    assert len(snap["history"]) == 1
