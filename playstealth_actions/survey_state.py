"""Tiny survey state tracker for PlayStealth.

Why: once HeyPiggy opens a second tab we need a single place to remember
which page is current, what step we are on, and what kind of flow we are in.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SurveyState:
    """Minimal runtime state for one survey session."""

    survey_index: int
    current_url: str = ""
    tab_count: int = 0
    step: int = 0
    mode: str = "list"
    events: list[str] = field(default_factory=list)

    def record(self, event: str) -> None:
        """Append a human-readable event for debug/traceability."""
        self.events.append(event)

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-friendly snapshot."""
        return {
            "survey_index": self.survey_index,
            "current_url": self.current_url,
            "tab_count": self.tab_count,
            "step": self.step,
            "mode": self.mode,
            "events": list(self.events),
        }


def create_state(survey_index: int) -> SurveyState:
    """Factory for a fresh survey state object."""
    return SurveyState(survey_index=survey_index)
