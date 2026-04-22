"""Canonical PlayStealth tool registry.

This is the living checklist of what the CLI can do (or should be able to do).
Each entry maps a tool name to its module and short purpose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """One PlayStealth tool entry."""

    name: str
    module: str
    purpose: str
    status: str = "implemented"


TOOLS: list[ToolSpec] = [
    ToolSpec(
        "open-list", "playstealth_actions.open_list", "Open HeyPiggy and show the survey list"
    ),
    ToolSpec("click-survey", "playstealth_actions.click_survey", "Click one survey card"),
    ToolSpec(
        "inspect-survey", "playstealth_actions.inspect_survey", "Inspect survey modal details"
    ),
    ToolSpec(
        "answer-survey", "playstealth_actions.answer_survey", "Choose one answer and continue"
    ),
    ToolSpec("run-survey", "playstealth_actions.run_survey", "Run a short survey loop"),
    ToolSpec(
        "resume-survey", "playstealth_actions.resume_survey", "Resume from the last saved state"
    ),
    ToolSpec("diagnose", "playstealth_actions.diagnostic_runner", "Run a browser diagnostics tool"),
    ToolSpec(
        "consent-modal", "playstealth_actions.consent_modal", "Accept post-open consent modals"
    ),
    ToolSpec(
        "page-follow",
        "playstealth_actions.page_utils",
        "Resolve the active page/tab after redirects",
    ),
    ToolSpec(
        "state-track", "playstealth_actions.survey_state", "Track current survey state and events"
    ),
    ToolSpec(
        "question-router", "playstealth_actions.question_router", "Route question types to handlers"
    ),
    ToolSpec(
        "radio-question",
        "playstealth_actions.radio_question",
        "Handle radio/checkbox survey questions",
    ),
    ToolSpec(
        "select-question",
        "playstealth_actions.select_question",
        "Handle select/dropdown questions",
        "implemented",
    ),
    ToolSpec(
        "text-question",
        "playstealth_actions.text_question",
        "Handle short text input questions",
        "implemented",
    ),
    ToolSpec(
        "textarea-question",
        "playstealth_actions.textarea_question",
        "Handle long free-text questions",
        "implemented",
    ),
    ToolSpec(
        "matrix-question",
        "playstealth_actions.matrix_question",
        "Handle matrix/grid questions",
        "implemented",
    ),
    ToolSpec(
        "slider-question",
        "playstealth_actions.slider_question",
        "Handle slider questions",
        "implemented",
    ),
    ToolSpec(
        "date-question", "playstealth_actions.date_question", "Handle date questions", "implemented"
    ),
    ToolSpec(
        "rank-order-question",
        "playstealth_actions.rank_order_question",
        "Handle rank-order questions",
        "implemented",
    ),
    ToolSpec(
        "number-question",
        "playstealth_actions.number_question",
        "Handle numeric input questions",
        "implemented",
    ),
    ToolSpec(
        "detect-question-type",
        "playstealth_actions.detect_question_type",
        "Detect the current question type",
        "implemented",
    ),
    ToolSpec(
        "detect-popup",
        "playstealth_actions.detect_popup",
        "Detect popups and overlays",
        "implemented",
    ),
    ToolSpec(
        "detect-new-tab",
        "playstealth_actions.detect_new_tab",
        "Detect when a survey opens a new tab",
        "implemented",
    ),
    ToolSpec(
        "detect-iframe",
        "playstealth_actions.detect_iframe",
        "Detect iframe-based survey embeds",
        "implemented",
    ),
    ToolSpec(
        "detect-spinner",
        "playstealth_actions.detect_spinner",
        "Detect loading spinners / stalls",
        "implemented",
    ),
    ToolSpec(
        "detect-consent",
        "playstealth_actions.detect_consent",
        "Detect consent dialogs",
        "implemented",
    ),
    ToolSpec(
        "inspect-page", "playstealth_actions.inspect_page", "Inspect the active page", "implemented"
    ),
    ToolSpec(
        "inspect-tabs", "playstealth_actions.inspect_tabs", "Inspect browser tabs", "implemented"
    ),
    ToolSpec(
        "inspect-controls",
        "playstealth_actions.inspect_controls",
        "Inspect visible controls",
        "implemented",
    ),
    ToolSpec("dump-state", "playstealth_actions.dump_state", "Dump session state", "implemented"),
    ToolSpec(
        "smart-click",
        "playstealth_actions.smart_actions",
        "Click with multi-strategy selector resolution",
        "implemented",
    ),
    ToolSpec(
        "check-stealth",
        "playstealth_actions.stealth_enhancer",
        "Run anti-detection leak checks",
        "implemented",
    ),
]


def list_tools() -> list[ToolSpec]:
    """Return the canonical tool list."""
    return list(TOOLS)
