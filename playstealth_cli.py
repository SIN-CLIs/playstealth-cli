#!/usr/bin/env python3
# ================================================================================
# DATEI: playstealth_cli.py
# PROJEKT: A2A-SIN-Worker-heyPiggy
# ZWECK: Kleine Playwright+Stealth CLI für reproduzierbare Survey-Clicks
# ================================================================================

"""PlayStealth CLI.

Why: Wir wollen eine kleine, stabile Oberfläche für genau die Dinge, die
in Playwright wirklich funktionieren:

* HeyPiggy Seite öffnen
* Survey-Liste sichtbar machen
* genau eine Survey-Kachel anklicken

Alles andere bleibt bewusst draußen.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

from playwright_stealth_worker import (
    _score_open_survey_candidate,
    detect_chrome_profile_dir,
    prepare_playwright_user_data_dir,
    wait_for_manual_login,
)

from playstealth_actions.answer_survey import run as answer_survey_run
from playstealth_actions.click_survey import run as click_survey_run
from playstealth_actions.click_card import run as click_card_run
from playstealth_actions.consent_modal import run as consent_modal_run
from playstealth_actions.browser_bootstrap import open_browser as open_browser_run
from playstealth_actions.inspect_modal import run as inspect_modal_run
from playstealth_actions.inspect_survey import run as inspect_survey_run
from playstealth_actions.list_cards import print_cards as print_cards_run
from playstealth_actions.page_utils import resolve_active_page
from playstealth_actions.open_list import run as open_list_run
from playstealth_actions.radio_question import run as radio_question_run
from playstealth_actions.survey_state import create_state
from playstealth_actions.run_survey import run as run_survey_run
from playstealth_actions.wait_question import run as wait_question_run

WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 768
HEYPIGGY_URL = "https://www.heypiggy.com/login?page=dashboard"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="playstealth", description="Playwright+Stealth helper CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    open_cmd = sub.add_parser("open-list", help="Open HeyPiggy and show the survey list")
    open_cmd.add_argument(
        "--timeout-seconds", type=int, default=300, help="How long to wait for manual login"
    )

    click_cmd = sub.add_parser("click-survey", help="Click one survey card")
    click_cmd.add_argument(
        "--timeout-seconds", type=int, default=300, help="How long to wait for manual login"
    )
    click_cmd.add_argument("--index", type=int, default=0, help="Survey card index after scoring")

    inspect_cmd = sub.add_parser(
        "inspect-survey", help="Open one survey and print modal/question details"
    )
    inspect_cmd.add_argument(
        "--timeout-seconds", type=int, default=300, help="How long to wait for manual login"
    )
    inspect_cmd.add_argument("--index", type=int, default=0, help="Survey card index after scoring")

    answer_cmd = sub.add_parser(
        "answer-survey", help="Pick one answer in the survey modal and continue"
    )
    answer_cmd.add_argument(
        "--timeout-seconds", type=int, default=300, help="How long to wait for manual login"
    )
    answer_cmd.add_argument("--index", type=int, default=0, help="Survey card index after scoring")
    answer_cmd.add_argument("--option-index", type=int, default=0, help="Answer option index")

    run_cmd = sub.add_parser(
        "run-survey", help="Open one survey and try to advance through modal questions"
    )
    run_cmd.add_argument(
        "--timeout-seconds", type=int, default=300, help="How long to wait for manual login"
    )
    run_cmd.add_argument("--index", type=int, default=0, help="Survey card index after scoring")
    run_cmd.add_argument(
        "--max-steps", type=int, default=20, help="Maximum survey-modal steps to attempt"
    )

    return parser


async def _open_browser():
    return await open_browser_run()


async def _wait_for_list(page, timeout_seconds: int) -> bool:
    return await wait_for_manual_login(page, timeout_seconds=timeout_seconds)


async def _print_cards(page, context) -> list[dict[str, str]]:
    return await print_cards_run(page, context)


async def _click_card(page, index: int):
    return await click_card_run(page, index)


async def _resolve_active_page(page):
    """Backward-compatible wrapper around the shared page helper."""
    return await resolve_active_page(page)


async def _inspect_survey(page) -> None:
    return await inspect_modal_run(page)


async def _answer_survey(page, option_index: int):
    """Choose one modal option and advance one step."""
    return await radio_question_run(page, option_index)


async def _wait_for_question_state(page, timeout_seconds: int = 10) -> bool:
    return await wait_question_run(page, timeout_seconds)


async def _run_open_list(timeout_seconds: int) -> int:
    playwright, context, page = await _open_browser()
    try:
        if await _wait_for_list(page, timeout_seconds):
            print("✅ Login erkannt")
        if page.is_closed() and context.pages:
            page = context.pages[0]
        await asyncio.sleep(1)
        await _print_cards(page, context)
        return 0
    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass


async def _run_click_survey(timeout_seconds: int, index: int) -> int:
    playwright, context, page = await _open_browser()
    try:
        if await _wait_for_list(page, timeout_seconds):
            print("✅ Login erkannt")
        if page.is_closed() and context.pages:
            page = context.pages[0]
        await asyncio.sleep(1)
        await _print_cards(page, context)
        page = await _click_card(page, index)
        return 0
    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass


async def _run_inspect_survey(timeout_seconds: int, index: int) -> int:
    playwright, context, page = await _open_browser()
    try:
        if await _wait_for_list(page, timeout_seconds):
            print("✅ Login erkannt")
        if page.is_closed() and context.pages:
            page = context.pages[0]
        await asyncio.sleep(1)
        await _print_cards(page, context)
        page = await _click_card(page, index)
        if page.is_closed() and context.pages:
            page = context.pages[-1]
        await asyncio.sleep(2)
        await _inspect_survey(page)
        return 0
    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass


async def _run_answer_survey(timeout_seconds: int, index: int, option_index: int) -> int:
    playwright, context, page = await _open_browser()
    try:
        if await _wait_for_list(page, timeout_seconds):
            print("✅ Login erkannt")
        if page.is_closed() and context.pages:
            page = context.pages[0]
        await asyncio.sleep(1)
        await _print_cards(page, context)
        page = await _click_card(page, index)
        if page.is_closed() and context.pages:
            page = context.pages[-1]
        await asyncio.sleep(2)
        page = await _answer_survey(page, option_index)
        return 0
    finally:
        try:
            await context.close()
        except Exception:
            pass


async def _run_survey_loop(timeout_seconds: int, index: int, max_steps: int) -> int:
    playwright, context, page = await _open_browser()
    state = create_state(index)
    try:
        if await _wait_for_list(page, timeout_seconds):
            print("✅ Login erkannt")
        if page.is_closed() and context.pages:
            page = context.pages[0]
        await asyncio.sleep(1)
        await _print_cards(page, context)
        page = await _click_card(page, index)
        if page.is_closed() and context.pages:
            page = context.pages[-1]
        state.mode = "opened"
        state.current_url = page.url
        state.tab_count = len(page.context.pages)
        state.record("survey opened")
        page = await _resolve_active_page(page)
        try:
            page = await consent_modal_run(page)
            state.record("consent handled")
        except Exception as consent_error:
            print(f"⚠️ Consent handling skipped/failed: {consent_error}")

        for step in range(max_steps):
            state.step = step + 1
            state.current_url = page.url
            state.tab_count = len(page.context.pages)
            await asyncio.sleep(1.5)
            modal = page.locator("#survey-modal")
            if not await modal.is_visible():
                print(f"✅ Survey modal closed after {step} steps")
                await _wait_for_question_state(page, timeout_seconds=10)
                try:
                    page = await consent_modal_run(page)
                except Exception as consent_error:
                    print(f"⚠️ Consent re-check skipped/failed: {consent_error}")
                try:
                    body = await page.locator("body").inner_text(timeout=3000)
                    print(f"🧾 Post-start body: {body[:800]!r}")
                except Exception as body_error:
                    print(f"⚠️ Post-start body error: {body_error}")
                try:
                    iframe = page.locator("iframe#frameurl")
                    if await iframe.count() > 0:
                        print(
                            f"🧩 frameurl src post-start: {await iframe.first.get_attribute('src')!r}"
                        )
                        print(
                            f"🧩 frameurl html post-start: {(await iframe.first.evaluate('el => el.outerHTML.slice(0, 500)'))!r}"
                        )
                except Exception as iframe_error:
                    print(f"⚠️ Post-start iframe error: {iframe_error}")
                break
            print(f"🔁 Survey step {step + 1}/{max_steps}")
            state.record(f"step_{step + 1}")
            await _inspect_survey(page)
            page = await _answer_survey(page, 0)
            state.current_url = page.url
            state.tab_count = len(page.context.pages)
            print(f"🧭 State snapshot: {state.snapshot()}")
        return 0
    finally:
        print(f"🧭 Final state: {state.snapshot()}")
        try:
            await context.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "open-list":
        return asyncio.run(open_list_run(args.timeout_seconds))
    if args.command == "click-survey":
        return asyncio.run(click_survey_run(args.timeout_seconds, args.index))
    if args.command == "inspect-survey":
        return asyncio.run(inspect_survey_run(args.timeout_seconds, args.index))
    if args.command == "answer-survey":
        return asyncio.run(answer_survey_run(args.timeout_seconds, args.index, args.option_index))
    if args.command == "run-survey":
        return asyncio.run(run_survey_run(args.timeout_seconds, args.index, args.max_steps))

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
