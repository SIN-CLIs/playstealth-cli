"""Runtime state machine -- issue #72."""
from opensin_runtime.state_machine import (
    RuntimeState,
    StateMachine,
    StateTransition,
    IllegalTransition,
)

__all__ = ["RuntimeState", "StateMachine", "StateTransition", "IllegalTransition"]
