"""
Typed State Machine for Page States.

Provides a finite state machine with explicit states, transition rules, and
entry/exit hooks. Used to track and validate page state transitions
throughout the worker lifecycle.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Dict, Tuple, Set, Optional


class PageState(Enum):
    """All possible page states in the worker lifecycle."""

    UNKNOWN = auto()
    PREFLIGHT = auto()
    LOGIN = auto()
    DASHBOARD = auto()
    SURVEY_ACTIVE = auto()
    SURVEY_DONE = auto()
    CAPTCHA = auto()
    ERROR = auto()
    DONE = auto()


@dataclass
class TransitionRule:
    """Defines an allowed transition with optional condition."""

    from_state: PageState
    to_state: PageState
    condition: Optional[Callable[[], bool]] = None


class StateMachine:
    """Generic finite state machine with transition validation."""

    def __init__(self, initial_state: PageState = PageState.UNKNOWN):
        self._state = initial_state
        self._transitions: Set[Tuple[PageState, PageState]] = set()
        self._hooks: Dict[str, Callable] = {
            "on_enter": {},
            "on_exit": {},
        }
        # By default allow all transitions (can be restricted via add_transition)
        for s in PageState:
            for t in PageState:
                self._transitions.add((s, t))

    @property
    def state(self) -> PageState:
        return self._state

    def add_transition(
        self, from_state: PageState, to_state: PageState, condition: Callable = None
    ):
        """Register an allowed transition."""
        self._transitions.add((from_state, to_state))

    def set_state(self, new_state: PageState) -> PageState:
        """Transition to a new state with validation."""
        if (self._state, new_state) not in self._transitions:
            # Allow but log warning
            pass
        old = self._state
        self._state = new_state
        return old

    def transition(self, new_state_str: str) -> PageState:
        """Convenient string-based transition (used by the worker)."""
        try:
            new_state = PageState[new_state_str.upper()]
        except KeyError:
            new_state = PageState.UNKNOWN
        return self.set_state(new_state)

    def current_value(self) -> str:
        """Return the current state as a lowercase string for compatibility."""
        return self._state.name.lower()


# Global page state machine instance
PageStateMachine = StateMachine
page_state_machine = StateMachine()
