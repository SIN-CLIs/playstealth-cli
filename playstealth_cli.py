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


async def _click_card(page, index: int) -> None:
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
        await _click_card(page, index)
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
        return asyncio.run(_run_open_list(args.timeout_seconds))
    if args.command == "click-survey":
        return asyncio.run(_run_click_survey(args.timeout_seconds, args.index))

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
