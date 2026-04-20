"""Deterministic worker state machine.

State graph::

    IDLE -> PLANNING -> ACTING -> OBSERVING -> IDLE
                          |           |
                          v           v
                       STALLED     CHALLENGED -> RECOVERING -> IDLE
                          |
                          v
                       FAILED (terminal)
                       DONE   (terminal)

Only transitions declared in ``_ALLOWED`` are accepted. Every transition
is stamped with a monotonic counter and optional metadata, so the
observability layer can replay an entire session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class RuntimeState(str, Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    OBSERVING = "OBSERVING"
    STALLED = "STALLED"
    CHALLENGED = "CHALLENGED"
    RECOVERING = "RECOVERING"
    DONE = "DONE"
    FAILED = "FAILED"


TERMINAL = {RuntimeState.DONE, RuntimeState.FAILED}

_ALLOWED: dict[RuntimeState, set[RuntimeState]] = {
    RuntimeState.IDLE: {RuntimeState.PLANNING, RuntimeState.FAILED},
    RuntimeState.PLANNING: {RuntimeState.ACTING, RuntimeState.FAILED, RuntimeState.DONE},
    RuntimeState.ACTING: {
        RuntimeState.OBSERVING,
        RuntimeState.STALLED,
        RuntimeState.CHALLENGED,
        RuntimeState.FAILED,
    },
    RuntimeState.OBSERVING: {
        RuntimeState.PLANNING,
        RuntimeState.DONE,
        RuntimeState.CHALLENGED,
        RuntimeState.FAILED,
        RuntimeState.IDLE,
    },
    RuntimeState.STALLED: {RuntimeState.RECOVERING, RuntimeState.FAILED},
    RuntimeState.CHALLENGED: {RuntimeState.RECOVERING, RuntimeState.FAILED},
    RuntimeState.RECOVERING: {
        RuntimeState.PLANNING,
        RuntimeState.IDLE,
        RuntimeState.FAILED,
    },
    RuntimeState.DONE: set(),
    RuntimeState.FAILED: set(),
}


class IllegalTransition(RuntimeError):
    def __init__(self, src: RuntimeState, dst: RuntimeState):
        super().__init__(f"illegal transition {src.value} -> {dst.value}")
        self.src = src
        self.dst = dst


@dataclass
class StateTransition:
    seq: int
    src: RuntimeState
    dst: RuntimeState
    ts: float
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class StateMachine:
    def __init__(self, *, on_transition: Callable[[StateTransition], None] | None = None) -> None:
        self._state = RuntimeState.IDLE
        self._seq = 0
        self._history: list[StateTransition] = []
        self._on_transition = on_transition or (lambda _t: None)

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL

    @property
    def history(self) -> list[StateTransition]:
        return list(self._history)

    def can_transition(self, dst: RuntimeState) -> bool:
        return dst in _ALLOWED.get(self._state, set())

    def transition(self, dst: RuntimeState, *, reason: str = "", **meta: Any) -> StateTransition:
        if self.is_terminal:
            raise IllegalTransition(self._state, dst)
        if dst not in _ALLOWED[self._state]:
            raise IllegalTransition(self._state, dst)
        self._seq += 1
        evt = StateTransition(
            seq=self._seq,
            src=self._state,
            dst=dst,
            ts=time.time(),
            reason=reason,
            meta=dict(meta),
        )
        self._state = dst
        self._history.append(evt)
        self._on_transition(evt)
        return evt

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "seq": self._seq,
            "history": [
                {
                    "seq": t.seq,
                    "src": t.src.value,
                    "dst": t.dst.value,
                    "ts": t.ts,
                    "reason": t.reason,
                    "meta": t.meta,
                }
                for t in self._history
            ],
        }
