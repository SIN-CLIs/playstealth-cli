#!/usr/bin/env python3
# ================================================================================
# DATEI: playwright_stealth_worker.py
# PROJEKT: A2A-SIN-Worker-heyPiggy
# ZWECK: Direkter Playwright+Stealth Worker - NICHT über Bridge!
# ================================================================================
"""
Nuclear Option - Playwright mit Stealth für HeyPiggy!

Dies ist ein eigenständiger Worker der:
1. Playwright mit Stealth nutzt
2. Erst CDP an ein laufendes Chrome versucht
3. Danach auf einen persistenten lokalen Profil-Clone fällt
4. Human-like clicks und keyboard navigation nutzt
5. Im Standard NICHT automatisch ins Login tippt

Usage:
    export HEYPIGGY_EMAIL="..."
    export HEYPIGGY_PASSWORD="..."
    export NVIDIA_API_KEY="..."
    python3 playwright_stealth_worker.py
"""

import asyncio
import os
import random
import shutil
import sys
import time
from pathlib import Path

# Füge Projekt-Pfad hinzu
sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth


CHROME_USER_DATA_DIR = Path.home() / "Library/Application Support/Google/Chrome"
PLAYWRIGHT_PROFILE_STORE = Path.home() / ".heypiggy" / "playwright_profile_clone"
WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 768


def detect_chrome_profile_dir() -> str:
    """Pick a Chrome profile dir, preferring the configured default."""
    preferred = os.environ.get("HEYPIGGY_CHROME_PROFILE_DIR", "Default")
    candidates = [preferred, "Default", "Profile 18"]
    for candidate in candidates:
        if (CHROME_USER_DATA_DIR / candidate).exists():
            return candidate
    return preferred


def prepare_playwright_user_data_dir() -> Path:
    """Create a persistent Playwright-safe clone of the active Chrome profile."""
    profile_dir = detect_chrome_profile_dir()
    clone_root = PLAYWRIGHT_PROFILE_STORE
    clone_root.mkdir(parents=True, exist_ok=True)

    dst_profile = clone_root / profile_dir
    if dst_profile.exists() and any(dst_profile.iterdir()):
        print(f"♻️ Wiederverwende persistentes Playwright-Profil: {clone_root}")
        print(f"🧭 Nutze Chrome-Profil: {profile_dir}")
        return clone_root

    for name in ("Local State", "First Run", "Last Version"):
        src = CHROME_USER_DATA_DIR / name
        if src.exists():
            shutil.copy2(src, clone_root / name)

    src_profile = CHROME_USER_DATA_DIR / profile_dir
    shutil.copytree(
        src_profile,
        dst_profile,
        symlinks=True,
        ignore_dangling_symlinks=True,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
            "RunningChromeVersion",
            "Crashpad",
            "GPUCache",
            "GrShaderCache",
            "ShaderCache",
            "Code Cache",
            "DawnCache",
            "Visited Links",
            "chrome_debug.log",
        ),
    )
    print(f"🧷 Playwright user-data clone: {clone_root}")
    print(f"🧭 Nutze Chrome-Profil: {profile_dir}")
    return clone_root


async def human_click(page, selector: str) -> bool:
    """Human-like click mit Mouse Down/Up."""
    try:
        element = await page.query_selector(selector)
        if element:
            box = await element.bounding_box()
            if box:
                # Bewege Maus wie Mensch (nicht instant!)
                await page.mouse.move(
                    box["x"] + box["width"] / 2 + random.randint(-10, 10),
                    box["y"] + box["height"] / 2 + random.randint(-10, 10),
                )
                await asyncio.sleep(random.uniform(0.1, 0.3))
                # Mouse down + up wie echter Mensch
                await page.mouse.down()
                await asyncio.sleep(random.uniform(0.05, 0.15))
                await page.mouse.up()
                return True
    except Exception as e:
        print(f"Click error: {e}")
    return False


async def keyboard_navigate_and_click(page):
    """Keyboard Navigation als Fallback."""
    await page.keyboard.press("Tab")
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.keyboard.press("Enter")
    await asyncio.sleep(1)


async def fill_input(page, selector: str, text: str):
    """Füllt Input mit menschlicher Geschwindigkeit."""
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.keyboard.type(text, delay=random.randint(50, 150))


async def wait_for_manual_login(page, timeout_seconds: int = 300) -> bool:
    """Wartet auf manuelles Login, bis die URL nicht mehr Login ist."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        current_url = (page.url or "").lower()
        if (
            "login" not in current_url and "signin" not in current_url
        ) or "page=dashboard" in current_url:
            return True
        await asyncio.sleep(1.5)
    return False


async def _score_survey_candidate(candidate) -> tuple[int, dict[str, str]]:
    """Bewerte einen potentiellen Survey-Entry nach Text/Href-Signalen."""
    try:
        text = (await candidate.inner_text(timeout=1500)).strip()
    except Exception:
        text = ""
    try:
        href = await candidate.get_attribute("href") or ""
    except Exception:
        href = ""
    haystack = f"{text} {href}".lower()
    score = 0
    for token, weight in [
        ("survey", 5),
        ("umfrage", 5),
        ("start", 4),
        ("starten", 4),
        ("begin", 3),
        ("take", 2),
        ("earn", 2),
        ("reward", 2),
        ("panel", 1),
        ("question", 1),
    ]:
        if token in haystack:
            score += weight
    return score, {"text": text, "href": href}


async def _score_open_survey_candidate(candidate) -> tuple[int, dict[str, str]]:
    """Bewerte einen echten Survey-Start-Kandidaten auf der Quellenliste."""
    try:
        text = (await candidate.inner_text(timeout=1500)).strip()
    except Exception:
        text = ""
    try:
        href = await candidate.get_attribute("href") or ""
    except Exception:
        href = ""
    haystack = f"{text} {href}".lower()
    if href.lower().startswith("javascript:show_page("):
        return -100, {"text": text, "href": href}
    score = 0
    for token, weight in [
        ("€", 6),
        ("minute", 5),
        ("min", 3),
        ("survey", 4),
        ("umfrage", 4),
        ("start", 3),
        ("begin", 2),
        ("open", 1),
        ("go", 1),
        ("http", 2),
    ]:
        if token in haystack:
            score += weight
    return score, {"text": text, "href": href}


async def main():
    """Haupt-Loop für HeyPiggy mit Playwright+Stealth."""

    print("🚀 Starte Playwright+Stealth Worker...")

    async with async_playwright() as p:
        browser = None
        context = None
        page = None
        connected_via_cdp = False

        try:
            print("🔌 Versuche an laufendes Chrome per CDP anzudocken...")
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            connected_via_cdp = True
            print("✅ CDP-Verbindung zu laufendem Chrome aktiv")

            context = browser.contexts[0] if browser.contexts else None
            if context is None:
                raise RuntimeError("CDP verbunden, aber kein Browser-Context gefunden")
            page = context.pages[0] if context.pages else await context.new_page()
        except Exception as cdp_error:
            print(f"⚠️ CDP fehlgeschlagen: {cdp_error}")
            print("🧩 Fallback: Playwright mit geklontem Profil")

            user_data_dir = prepare_playwright_user_data_dir()
            profile_dir = detect_chrome_profile_dir()
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
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

        # Aktiviere Stealth
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        print("✅ Stealth aktiviert")

        # Gehe zu HeyPiggy Login
        print("🌐 Navigiere zu HeyPiggy...")
        await page.goto("https://www.heypiggy.com/login")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        # Check ob Login Seite
        print(f"📄 Aktuelle URL: {page.url}")

        email = os.environ.get("HEYPIGGY_EMAIL", "")
        password = os.environ.get("HEYPIGGY_PASSWORD", "")
        automated_login = (
            os.environ.get("HEYPIGGY_AUTOMATED_LOGIN", "0") == "1"
            and bool(email)
            and bool(password)
        )
        if not automated_login:
            print("🖐️ Manueller Login-Modus aktiv — ich tippe nichts ins Login-Formular.")

        if automated_login:
            # Finde Email Input
            email_selectors = [
                'input[type="email"]',
                'input[name="email"]',
                'input[id="email"]',
                'input[placeholder*="email"]',
                'input[placeholder*="E-Mail"]',
            ]

            email_filled = False
            for sel in email_selectors:
                try:
                    if await page.query_selector(sel):
                        await fill_input(page, sel, email)
                        email_filled = True
                        print(f"✅ Email eingegeben: {email[:5]}...")
                        break
                except:
                    continue

            if not email_filled:
                print("❌ Email Input nicht gefunden!")
                # Screenshot für Debug
                await page.screenshot(path="/tmp/heypiggy_debug.png")
                print(f"📸 Screenshot: /tmp/heypiggy_debug.png")

            # Password
            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[id="password"]',
            ]

            for sel in password_selectors:
                try:
                    if await page.query_selector(sel):
                        await fill_input(page, sel, password)
                        print("✅ Password eingegeben")
                        break
                except:
                    continue

            # Login Button
            button_selectors = [
                'button[type="submit"]',
                'button:has-text("Anmelden")',
                'button:has-text("Login")',
                'button:has-text("Sign in")',
                'input[type="submit"]',
            ]

            login_success = False
            for sel in button_selectors:
                try:
                    # Erst keyboard navigation versuchen
                    await page.keyboard.press("Tab")
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(1)

                    # Check ob wir eingeloggt sind
                    if "dashboard" in page.url.lower() or "login" not in page.url.lower():
                        login_success = True
                        print("✅ Login erfolgreich!")
                        break

                    # Sonst Button klicken
                    if await human_click(page, sel):
                        await asyncio.sleep(2)
                        if "dashboard" in page.url.lower():
                            login_success = True
                            print("✅ Login Button geklickt - erfolgreich!")
                            break
                except Exception as e:
                    print(f"Button click error: {e}")
                    continue
        else:
            print("🛑 Auto-Login deaktiviert — bitte jetzt manuell im geöffneten Browser anmelden.")
            login_success = await wait_for_manual_login(page, timeout_seconds=300)
            if login_success:
                print("✅ Manuelles Login erkannt!")
            else:
                print("⏳ Login nicht erkannt — fahre trotzdem fort und prüfe Dashboard.")

        # Dashboard
        print(f"📄 URL nach Login: {page.url}")

        # Warte auf Dashboard Inhalt
        await asyncio.sleep(3)

        # Suche nach verfügbaren Umfragen
        survey_selectors = [
            'a[href*="survey"]',
            'a[href*="umfrage"]',
            ".survey-card",
            "[data-survey]",
            'button:has-text("Umfrage starten")',
        ]

        surveys_found = False
        for sel in survey_selectors:
            try:
                elements = await page.query_selector_all(sel)
                if elements:
                    print(f"✅ {len(elements)} Umfragen gefunden mit: {sel}")
                    surveys_found = True

                    # Wähle den besten sichtbaren Einstieg anhand von Text/Href.
                    best_score = -1
                    target = None
                    for candidate in elements:
                        try:
                            if not await candidate.is_visible():
                                continue
                            score, meta = await _score_survey_candidate(candidate)
                            print(
                                f"🔎 Kandidat: score={score} text={meta['text'][:80]!r} href={meta['href']!r}"
                            )
                            if score > best_score:
                                best_score = score
                                target = candidate
                        except Exception:
                            continue
                    if target is None:
                        target = elements[0]

                    # Klicke auf den gewählten Einstieg (genau ein Nutzerklick)
                    clicked = False
                    try:
                        await target.scroll_into_view_if_needed(timeout=4000)
                        await asyncio.sleep(0.5)
                        await target.click(timeout=4000, force=True)
                        clicked = True
                    except Exception as first_click_error:
                        print(f"⚠️ Force-click fehlgeschlagen: {first_click_error}")
                        try:
                            await target.evaluate("el => el.click()")
                            clicked = True
                        except Exception as js_click_error:
                            print(f"⚠️ JS-click fehlgeschlagen: {js_click_error}")

                    await asyncio.sleep(3)
                    if context is not None and len(context.pages) > 1:
                        page = context.pages[-1]
                        print(f"🪟 Neuer Tab erkannt: {page.url}")
                    try:
                        body_text = await page.locator("body").inner_text(timeout=3000)
                        print(f"🧾 Body-Snippet: {body_text[:300]!r}")
                    except Exception as body_error:
                        print(f"⚠️ Body-Text nicht lesbar: {body_error}")
                    if "deine verfügbaren quellen" in body_text.lower():
                        # OBSERVATION: Der erste Klick auf "Umfragen" öffnet nur die
                        # Survey-Liste. Der eigentliche Einstieg ist das survey-item
                        # mit onclick=clickSurvey('<id>'). Darum priorisieren wir unten
                        # explizit survey_list / survey-item statt erneut den Sidebar-Link.
                        survey_cards = page.locator("#survey_list .survey-item")
                        card_count = await survey_cards.count()
                        print(f"🪪 Survey-Item Cards: {card_count}")
                        best_card = None
                        best_card_score = -1
                        for i in range(min(card_count, 20)):
                            card = survey_cards.nth(i)
                            try:
                                if not await card.is_visible():
                                    continue
                                card_score, card_meta = await _score_open_survey_candidate(card)
                                print(
                                    f"🎯 Card: score={card_score} text={card_meta['text'][:80]!r} href={card_meta['href']!r}"
                                )
                                if card_score > best_card_score:
                                    best_card_score = card_score
                                    best_card = card
                            except Exception:
                                continue

                        if best_card is not None:
                            try:
                                best_card_onclick = await best_card.get_attribute("onclick") or ""
                                await best_card.scroll_into_view_if_needed(timeout=4000)
                                await asyncio.sleep(0.5)
                                # Survey-Starts öffnen oft ein Popup/नई Seite. Wir fangen das
                                # explizit ab, damit ein erfolgreicher Klick nicht wie ein
                                # "No-op" aussieht.
                                popup_page = None
                                try:
                                    async with page.expect_popup(timeout=5000) as popup_info:
                                        await best_card.dispatch_event("click")
                                    popup_page = await popup_info.value
                                except Exception:
                                    await best_card.dispatch_event("click")
                                await asyncio.sleep(3)
                                try:
                                    body_text = await page.locator("body").inner_text(timeout=3000)
                                    print(f"🧾 Nach-Card-Click Body: {body_text[:300]!r}")
                                except Exception:
                                    body_text = ""
                                if (
                                    "deine verfügbaren quellen" in body_text.lower()
                                    or "survey_list" in body_text.lower()
                                ) and "clickSurvey(" in best_card_onclick:
                                    survey_id = best_card_onclick.split("clickSurvey('")[1].split(
                                        "')"
                                    )[0]
                                    print(f"🔧 Direct clickSurvey fallback: {survey_id}")
                                    click_result = await page.evaluate(
                                        "sid => clickSurvey(sid)", survey_id
                                    )
                                    print(f"🧠 clickSurvey result: {click_result!r}")
                                    await asyncio.sleep(3)
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
                                    try:
                                        body_text = await page.locator("body").inner_text(
                                            timeout=3000
                                        )
                                        print(
                                            f"🧾 Nach-direct clickSurvey Body: {body_text[:300]!r}"
                                        )
                                    except Exception:
                                        pass
                                if popup_page is not None:
                                    try:
                                        await popup_page.wait_for_load_state(
                                            "domcontentloaded", timeout=5000
                                        )
                                    except Exception:
                                        pass
                                    print(f"🪟 Popup-Page erkannt: {popup_page.url}")
                                    page = popup_page
                                    try:
                                        popup_text = await popup_page.locator("body").inner_text(
                                            timeout=3000
                                        )
                                        print(f"🧾 Popup-Body: {popup_text[:300]!r}")
                                    except Exception as popup_error:
                                        print(f"⚠️ Popup-Body nicht lesbar: {popup_error}")
                                print(f"📄 URL nach Card-Click: {page.url}")
                            except Exception as card_click_error:
                                print(f"⚠️ Card-Click fehlgeschlagen: {card_click_error}")
                                try:
                                    onclick = await best_card.get_attribute("onclick") or ""
                                    if "clickSurvey(" in onclick:
                                        survey_id = onclick.split("clickSurvey('")[1].split("')")[0]
                                        await page.evaluate("sid => clickSurvey(sid)", survey_id)
                                        await asyncio.sleep(3)
                                        body_text = await page.locator("body").inner_text(
                                            timeout=3000
                                        )
                                        print(f"🧾 Nach-clickSurvey Body: {body_text[:300]!r}")
                                        print(f"📄 URL nach clickSurvey: {page.url}")
                                except Exception as invoke_error:
                                    print(f"⚠️ clickSurvey-Aufruf fehlgeschlagen: {invoke_error}")
                        clickable = page.locator("a, button, [role='button']")
                        total = await clickable.count()
                        best_score = -1
                        best_el = None
                        print(f"🔎 Survey-List Kandidaten: {total}")
                        for i in range(min(total, 40)):
                            candidate = clickable.nth(i)
                            try:
                                if not await candidate.is_visible():
                                    continue
                                score, meta = await _score_open_survey_candidate(candidate)
                                if score <= 0:
                                    continue
                                print(
                                    f"🔎 Open-Kandidat: score={score} text={meta['text'][:100]!r} href={meta['href']!r}"
                                )
                                if score > best_score:
                                    best_score = score
                                    best_el = candidate
                            except Exception:
                                continue
                        try:
                            survey_nodes = await page.evaluate(
                                """
                                () => Array.from(document.querySelectorAll('*'))
                                  .filter(el => {
                                    const t = (el.innerText || '').trim();
                                    if (!t) return false;
                                    if (!/[€]/.test(t) || !/Minute/i.test(t)) return false;
                                    const r = el.getBoundingClientRect();
                                    if (r.width < 20 || r.height < 20) return false;
                                    const idClass = `${el.id || ''} ${el.className || ''}`.toLowerCase();
                                    if (idClass.includes('sidebar')) return false;
                                    if (idClass.includes('menue_button_sidebar')) return false;
                                    return true;
                                  })
                                  .slice(0, 10)
                                  .map(el => ({
                                    tag: el.tagName,
                                    text: (el.innerText || '').trim().slice(0, 120),
                                    href: el.getAttribute('href') || '',
                                    onclick: el.getAttribute('onclick') || '',
                                    role: el.getAttribute('role') || '',
                                    id: el.id || '',
                                    cls: el.className || '',
                                  }))
                                """
                            )
                            print(f"🧩 Survey-Nodes: {survey_nodes}")
                        except Exception as node_error:
                            print(f"⚠️ Survey-Node-Scan fehlgeschlagen: {node_error}")
                        if best_el is not None:
                            try:
                                await best_el.scroll_into_view_if_needed(timeout=4000)
                                await asyncio.sleep(0.5)
                                await best_el.click(timeout=4000, force=True)
                                await asyncio.sleep(3)
                                try:
                                    body_text = await page.locator("body").inner_text(timeout=3000)
                                    print(f"🧾 Nach-Start Body: {body_text[:300]!r}")
                                except Exception:
                                    pass
                                print(f"📄 URL nach Survey-Start: {page.url}")
                            except Exception as open_click_error:
                                print(f"⚠️ Survey-Start Klick fehlgeschlagen: {open_click_error}")
                    print(f"📄 URL nach Umfrage-Klick: {page.url}")
                    print(f"🧷 Klick-Status: {'ok' if clicked else 'failed'}")
                    if context is not None:
                        await context.close()
                    if browser is not None and not connected_via_cdp:
                        await browser.close()
                    return
            except Exception:
                continue

        if not surveys_found:
            print("⚠️ Keine Umfragen auf Dashboard gefunden")
            await page.screenshot(path="/tmp/heypiggy_dashboard.png")

        # Halte Browser offen für Debug
        print("⏸️ Browser bleibt offen für Debug...")
        print("Drücke Ctrl+C zum Beenden")

        # Warte ewig (für Debug)
        await asyncio.sleep(300)

        if context is not None:
            await context.close()
        if browser is not None and not connected_via_cdp:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
