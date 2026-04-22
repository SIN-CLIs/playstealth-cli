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
from playstealth_actions.consent_modal import run as consent_modal_run
from playstealth_actions.inspect_survey import run as inspect_survey_run
from playstealth_actions.page_utils import resolve_active_page
from playstealth_actions.open_list import run as open_list_run
from playstealth_actions.radio_question import run as radio_question_run
from playstealth_actions.run_survey import run as run_survey_run

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
    profile_root = prepare_playwright_user_data_dir()
    profile_dir = detect_chrome_profile_dir()

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_root),
        channel="chrome",
        headless=False,
        args=[
            f"--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            f"--profile-directory={profile_dir}",
        ],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.set_viewport_size({"width": WINDOW_WIDTH, "height": WINDOW_HEIGHT})

    stealth = Stealth()
    await stealth.apply_stealth_async(page)
    await page.goto(HEYPIGGY_URL)
    await page.wait_for_load_state("domcontentloaded")
    return playwright, context, page


async def _wait_for_list(page, timeout_seconds: int) -> bool:
    return await wait_for_manual_login(page, timeout_seconds=timeout_seconds)


async def _print_cards(page, context) -> list[dict[str, str]]:
    if page.is_closed() and context.pages:
        page = context.pages[0]
    body_text = await page.locator("body").inner_text(timeout=3000)
    print(f"🧾 Body: {body_text[:250]!r}")
    cards = page.locator("#survey_list .survey-item")
    count = await cards.count()
    print(f"🪪 Survey cards: {count}")
    scored: list[dict[str, str]] = []
    for i in range(min(count, 20)):
        card = cards.nth(i)
        if not await card.is_visible():
            continue
        score, meta = await _score_open_survey_candidate(card)
        print(f"🎯 Card[{i}]: score={score} text={meta['text'][:90]!r}")
        scored.append({"index": str(i), "score": str(score), **meta})
    return scored


async def _click_card(page, index: int):
    cards = page.locator("#survey_list .survey-item")
    count = await cards.count()
    if count == 0:
        raise RuntimeError("No survey cards found")

    try:
        fn_src = await page.evaluate(
            "() => (window.clickSurvey ? window.clickSurvey.toString() : 'missing')"
        )
        print(f"🧪 clickSurvey src: {fn_src[:1200]!r}")
        survey_list_probe = await page.evaluate(
            """
            () => {
              const list = window.acctualSurveyList;
              const first = Array.isArray(list) && list.length ? list[0] : null;
              return {
                hasList: Array.isArray(list),
                length: Array.isArray(list) ? list.length : 0,
                firstType: first ? typeof first.id : 'none',
                firstId: first ? first.id : null,
                firstKeys: first ? Object.keys(first).slice(0, 12) : [],
              };
            }
            """
        )
        print(f"🧪 surveyList probe: {survey_list_probe}")
    except Exception as fn_error:
        print(f"⚠️ clickSurvey-src fehlgeschlagen: {fn_error}")

    best = cards.nth(min(index, count - 1))
    onclick = await best.get_attribute("onclick") or ""
    await best.scroll_into_view_if_needed(timeout=4000)
    try:
        await best.dispatch_event("click")
    except Exception:
        await best.click(timeout=4000, force=True)

    await asyncio.sleep(2)
    print(f"📄 URL nach Klick: {page.url}")
    if "clickSurvey(" in onclick:
        sid = onclick.split("clickSurvey('")[1].split("')")[0]
        print(f"🧷 card onclick survey id: {sid}")
        result = await page.evaluate("sid => clickSurvey(sid)", sid)
        print(f"🧠 clickSurvey result: {result!r}")
        await asyncio.sleep(3)
        page_urls = [pg.url for pg in page.context.pages]
        print(f"🪟 Pages nach clickSurvey: {page_urls}")
        try:
            overlays = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll('iframe, [role="dialog"], .modal, .overlay'))
                  .map(el => ({
                    tag: el.tagName,
                    id: el.id || '',
                    cls: el.className || '',
                    text: (el.innerText || '').trim().slice(0, 120),
                    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                  }))
                """
            )
            print(f"🪟 Overlays/Iframes: {overlays}")
        except Exception as overlay_error:
            print(f"⚠️ Overlay-Scan fehlgeschlagen: {overlay_error}")
        print(f"📄 URL nach clickSurvey: {page.url}")
    await asyncio.sleep(1)
    if len(page.context.pages) > 1:
        for candidate in reversed(page.context.pages):
            if not candidate.is_closed() and candidate.url != "about:blank":
                print(f"🪟 Active page after click: {candidate.url}")
                return candidate
        print(f"🪟 Active page after click: {page.context.pages[-1].url}")
        return page.context.pages[-1]
    return page


async def _resolve_active_page(page):
    """Backward-compatible wrapper around the shared page helper."""
    return await resolve_active_page(page)


async def _inspect_survey(page) -> None:
    """Dump the visible survey modal so we can wire the next CLI step."""
    modal = page.locator("#survey-modal")
    try:
        visible = await modal.is_visible()
        print(f"🪟 survey-modal visible: {visible}")
        if visible:
            print(f"🧾 survey-modal text: {(await modal.inner_text(timeout=3000))[:600]!r}")
            controls = modal.locator("button, input, label, [role='button']")
            count = await controls.count()
            print(f"🎛️ survey-modal controls: {count}")
            for i in range(min(count, 24)):
                ctl = controls.nth(i)
                try:
                    txt = (await ctl.inner_text(timeout=1200)).strip()
                except Exception:
                    txt = ""
                try:
                    val = await ctl.get_attribute("value") or ""
                except Exception:
                    val = ""
                try:
                    outer = await ctl.evaluate(
                        "el => ({tag: el.tagName, id: el.id || '', cls: el.className || '', name: el.getAttribute('name') || '', onclick: el.getAttribute('onclick') || '', type: el.getAttribute('type') || '', text: (el.innerText || '').trim().slice(0, 120), html: el.outerHTML.slice(0, 300)})"
                    )
                except Exception:
                    outer = {
                        "tag": "?",
                        "id": "",
                        "cls": "",
                        "name": "",
                        "onclick": "",
                        "type": "",
                        "text": txt[:120],
                        "html": "",
                    }
                print(f"   • control[{i}]: {outer}")
        try:
            iframe = page.locator("iframe#frameurl")
            if await iframe.count() > 0:
                print(f"🧩 frameurl src: {await iframe.first.get_attribute('src')!r}")
                print(f"🧩 frameurl name: {await iframe.first.get_attribute('name')!r}")
                print(
                    f"🧩 frameurl outer: {(await iframe.first.evaluate('el => el.outerHTML.slice(0, 300)'))!r}"
                )
        except Exception as iframe_error:
            print(f"⚠️ frameurl inspect failed: {iframe_error}")
        frames = page.frames
        print(f"🪟 frames: {len(frames)}")
        for i, frame in enumerate(frames[:6]):
            try:
                frame_url = frame.url
            except Exception:
                frame_url = "<unavailable>"
            print(f"   • frame[{i}] url={frame_url}")
            if frame_url and "frameurl" in frame_url:
                try:
                    txt = await frame.locator("body").inner_text(timeout=3000)
                    print(f"   • frame[{i}] body: {txt[:500]!r}")
                except Exception as frame_error:
                    print(f"   • frame[{i}] body error: {frame_error}")
    except Exception as inspect_error:
        print(f"⚠️ Survey modal inspect failed: {inspect_error}")


async def _answer_survey(page, option_index: int):
    """Choose one modal option and advance one step."""
    return await radio_question_run(page, option_index)


async def _wait_for_question_state(page, timeout_seconds: int = 10) -> bool:
    """Wait until the consent modal turns into a real question or iframe loads."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            modal_text = (await page.locator("#survey-modal").inner_text(timeout=1000)).lower()
        except Exception:
            modal_text = ""
        try:
            iframe_src = await page.locator("iframe#frameurl").get_attribute("src") or ""
        except Exception:
            iframe_src = ""
        if iframe_src.strip():
            print(f"🧩 frameurl src became active: {iframe_src!r}")
            return True
        if "umfrage starten" not in modal_text and "du kannst jetzt" not in modal_text:
            print(f"🧩 survey-modal changed: {modal_text[:200]!r}")
            return True
        await asyncio.sleep(1)
    return False


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
        page = await _resolve_active_page(page)
        try:
            page = await consent_modal_run(page)
        except Exception as consent_error:
            print(f"⚠️ Consent handling skipped/failed: {consent_error}")

        for step in range(max_steps):
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
            await _inspect_survey(page)
            page = await _answer_survey(page, 0)
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
