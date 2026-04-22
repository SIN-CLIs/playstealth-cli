"""Einfacher Survey Runner - Fokus auf Zuverlässigkeit statt Komplexität.

WICHTIG:
- Dieser Runner ist der minimalistische URL-basierte Live-Pfad.
- Wenn bereits eine konkrete Survey-URL bekannt ist, ist dieser Pfad oft
  robuster als ein größerer Dashboard-Loop.
- Er nutzt Plugins, Telemetrie, Trap-Detection und State-Persistenz, bleibt
  aber bewusst kleiner und einfacher als die Dashboard-Orchestrierung.
"""
import time
from typing import Optional, Dict, Any
from playwright.async_api import Page, BrowserContext

from .answer_strategies import get_strategy
from .simple_selector import safe_click
from .human_delay import fast_delay, medium_delay, slow_delay
from .state_store import save_cli_state, save_browser_state, load_cli_state
from .telemetry import log_event, generate_session_id
from .trap_detector import analyze_page_traps
from .plugins.loader import load_plugins, detect_platform


async def _extract_option_texts(page: Page) -> list[str]:
    """Collect visible option texts for strategy and trap logic.

    We stay intentionally generic here because the simple runner is designed to
    work across many survey pages without requiring panel-specific DOM code.
    """
    locators = await page.locator(
        "label, .option, .q-radio, .q-checkbox, [role='radio'], [role='checkbox']"
    ).all()
    texts: list[str] = []
    for locator in locators:
        try:
            text = (await locator.inner_text()).strip()
            if text:
                texts.append(text)
        except Exception:
            continue
    return texts


async def _read_question_text(page: Page, platform=None) -> str:
    """Read the current question text from platform hooks or generic selectors."""
    if platform is not None:
        try:
            step = await platform.get_current_step(page)
            text = str(step.get("question", "")).strip()
            if text:
                return text
        except Exception:
            pass

    selectors = [
        ".question-title",
        "h1",
        "h2",
        "h3",
        ".QuestionText",
        ".q-text",
        "[class*='question']",
    ]
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if await loc.count() > 0:
                text = (await loc.inner_text(timeout=1500)).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def run_survey_step(
    page: Page,
    step_index: int,
    context: Optional[BrowserContext] = None,
    session_id: Optional[str] = None,
    strategy=None,
    platform=None,
) -> bool:
    """
    Führt einen einzelnen Survey-Schritt aus mit robusten Selektoren und Delays.
    
    Returns:
        True wenn erfolgreich, False wenn fehlgeschlagen oder Ende erreicht
    """
    start_time = time.perf_counter()
    
    try:
        # Prüfen ob Survey noch existiert.
        # Wenn ein Plugin erkannt wurde, vertrauen wir stärker auf dessen
        # get_current_step()-Logik und nicht nur auf generische Schlüsselwörter.
        page_content = await page.content()
        question_hint = await _read_question_text(page, platform)
        if platform is None and "survey" not in page_content.lower() and "question" not in page_content.lower() and not question_hint:
            print(f"   ℹ️  Step {step_index}: Keine Survey-Elemente erkannt - möglicherweise beendet")
            return False

        # Plugin-first path: if we know the platform, use its purpose-built hooks.
        if platform is not None:
            try:
                step = await platform.get_current_step(page)
                question_text = step.get("question", "") or question_hint
                option_count = int(step.get("option_count", 0))
                option_texts = await _extract_option_texts(page)
                trap = await analyze_page_traps(page, question_text, option_texts)

                if trap["attention_check"]:
                    log_event(
                        session_id or "unknown",
                        "trap_hit",
                        platform=platform.__class__.__name__,
                        step_index=step_index,
                        trap_type="attention_check",
                        metadata={
                            "instruction": trap["attention_check"].get("instruction", "")[:120]
                        },
                    )
                if trap["honeypots"]:
                    log_event(
                        session_id or "unknown",
                        "trap_hit",
                        platform=platform.__class__.__name__,
                        step_index=step_index,
                        trap_type="honeypot",
                        metadata={"count": len(trap["honeypots"])},
                    )

                answer_idx = 0
                if strategy is not None:
                    answer_idx = await strategy.choose(question_text, option_count, option_texts)
                if trap["attention_check"] and trap["attention_check"].get("action") == "select_index":
                    answer_idx = int(trap["attention_check"]["index"])
                if option_count > 0:
                    answer_idx = max(0, min(int(answer_idx), option_count - 1))

                answered = await platform.answer_question(page, answer_idx)
                if answered:
                    await fast_delay()
                    await platform.navigate_next(page)
                    await medium_delay()
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    log_event(
                        session_id or "unknown",
                        "step_end",
                        platform=platform.__class__.__name__,
                        step_index=step_index,
                        duration_ms=duration_ms,
                        success=True,
                        metadata={"question_type": step.get("type", "unknown")},
                    )
                    return True
            except Exception as e:
                # WICHTIG: Plugin-Fehler nicht verschlucken. Ein kurzes Log hilft,
                # damit Entwickler sehen, warum wir in den generischen Fallback
                # gerutscht sind.
                print(f"   ⚠️  Plugin-Pfad fehlgeschlagen: {e}")
        
        # Typische Survey-Elemente finden und interagieren.
        # WICHTIG: Antworten kommen vor "Weiter", sonst würden wir zu früh
        # navigieren und Questions ohne Antwort überspringen.
        next_buttons = ["Weiter", "Next", "Continue", "Fortfahren", "→", "»"]

        # 1. Nach Radio-Buttons / Checkboxen suchen (Antwort-Optionen)
        radio_selectors = [
            'input[type="radio"]',
            'input[type="checkbox"]',
            '[role="radio"]',
            '[role="checkbox"]'
        ]
        
        for selector in radio_selectors:
            try:
                options = page.locator(selector)
                count = await options.count()
                if count > 0:
                    # Zufällige Option wählen (nicht immer die erste)
                    question_text = question_hint or await _read_question_text(page, platform)
                    option_texts = await _extract_option_texts(page)
                    trap = await analyze_page_traps(page, question_text, option_texts)
                    if trap["attention_check"] and trap["attention_check"].get("action") == "select_index":
                        option_index = int(trap["attention_check"]["index"])
                    elif strategy is not None:
                        option_index = await strategy.choose(question_text, count, option_texts)
                    else:
                        import random
                        option_index = random.randint(0, min(count - 1, 2))  # Max Index 2
                    option_index = max(0, min(int(option_index), count - 1))
                    await options.nth(option_index).click()
                    await fast_delay()
                    print(f"   ✓ Step {step_index}: Option {option_index + 1} gewählt ({selector})")
                    
                    # Nach Auswahl kurz warten dann weiter
                    await slow_delay()
                    
                    # Versuch automatisch auf Weiter zu klicken
                    for btn_text in next_buttons:
                        if await safe_click(page, btn_text):
                            print(f"   ✓ Step {step_index}: '{btn_text}' nach Antwort geklickt")
                            await medium_delay()
                            duration_ms = (time.perf_counter() - start_time) * 1000
                            log_event(
                                session_id or "unknown",
                                "step_end",
                                step_index=step_index,
                                duration_ms=duration_ms,
                                success=True,
                                metadata={"action": f"answered_and_clicked_{btn_text}"},
                            )
                            return True
                    
                    # Kein Weiter-Button gefunden, aber Antwort gegeben
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    log_event(
                        session_id or "unknown",
                        "step_end",
                        step_index=step_index,
                        duration_ms=duration_ms,
                        success=True,
                        metadata={"action": f"answered_{selector}"},
                    )
                    return True
            except Exception:
                continue

        # 2. Nach "Weiter" / "Next" Buttons suchen
        next_buttons = ["Weiter", "Next", "Continue", "Fortfahren", "→", "»"]
        for btn_text in next_buttons:
            if await safe_click(page, btn_text):
                print(f"   ✓ Step {step_index}: '{btn_text}' geklickt")
                await medium_delay()
                duration_ms = (time.perf_counter() - start_time) * 1000
                log_event(
                    session_id or "unknown",
                    "step_end",
                    step_index=step_index,
                    duration_ms=duration_ms,
                    success=True,
                    metadata={"action": f"clicked_{btn_text}"},
                )
                return True
        
        # 3. Nach Text-Input Feldern suchen
        text_inputs = [
            'input[type="text"]',
            'input[type="email"]',
            'textarea',
            '[contenteditable="true"]'
        ]
        
        for selector in text_inputs:
            try:
                inputs = page.locator(selector)
                count = await inputs.count()
                if count > 0:
                    # Beispielhaftes Ausfüllen
                    await inputs.first.fill("Test Antwort")
                    await fast_delay()
                    print(f"   ✓ Step {step_index}: Textfeld ausgefüllt ({selector})")
                    
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    log_event(
                        session_id or "unknown",
                        "step_end",
                        step_index=step_index,
                        duration_ms=duration_ms,
                        success=True,
                        metadata={"action": f"filled_{selector}"},
                    )
                    return True
            except Exception:
                continue
        
        # 4. Falls nichts gefunden wurde - Seite screenshot für Debugging
        print(f"   ⚠️  Step {step_index}: Keine interaktiven Elemente gefunden")
        
        # Versuch einfach zu scrollen als Fallback
        await page.evaluate("window.scrollBy(0, 300)")
        await slow_delay()
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_event(
            session_id or "unknown",
            "step_end",
            step_index=step_index,
            duration_ms=duration_ms,
            success=False,
            error_code="no_interactive_elements"
        )
        return False
        
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        log_event(
            session_id or "unknown",
            "step_end",
            step_index=step_index,
            duration_ms=duration_ms,
            success=False,
            error_code=str(e)
        )
        print(f"   ❌ Step {step_index}: Fehler - {e}")
        return False


async def execute_survey_flow(
    page: Page,
    context: BrowserContext,
    start_url: str,
    max_steps: int = 10,
    session_id: Optional[str] = None,
    strategy_name: str = "persona",
    strategy_persona: str = "neutral",
) -> Dict[str, Any]:
    """
    Haupt-Survey-Flow mit State-Persistenz nach jedem Schritt.
    
    Returns:
        Dictionary mit Ergebnissen und Status
    """
    if session_id is None:
        session_id = generate_session_id()

    strategy = get_strategy(strategy_name, persona=strategy_persona)
    platform = None
    
    print(f"🚀 Starte Survey-Flow (Session: {session_id}, Max Steps: {max_steps})")
    
    # Initiale Navigation
    try:
        current_content = (await page.content()).strip()
        if start_url and not (start_url == "about:blank" and current_content):
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
            await medium_delay()
            print(f"   ✓ Seite geladen: {start_url}")
        else:
            print("   ✓ Verwende bereits geladene Survey-Seite")
        try:
            plugins = load_plugins()
            platform = await detect_platform(page, plugins)
            print(f"   🔌 Plattform erkannt: {platform.__class__.__name__}")
        except Exception:
            platform = None
    except Exception as e:
        print(f"   ❌ Fehler beim Laden der URL: {e}")
        return {
            "success": False,
            "session_id": session_id,
            "error": f"Navigation failed: {e}",
            "steps_completed": 0
        }
    
    steps_completed = 0
    
    for step in range(1, max_steps + 1):
        log_event(session_id, "step_start", step_index=step)
        
        success = await run_survey_step(page, step, context, session_id, strategy=strategy, platform=platform)
        
        if success:
            steps_completed = step
            
            # State nach jedem erfolgreichen Schritt speichern
            try:
                await save_browser_state(context, session_id)
                save_cli_state(session_id, {
                    "step_index": step,
                    "url": page.url,
                    "timestamp": time.time(),
                    "status": "in_progress"
                })
            except Exception as e:
                print(f"   ⚠️  State speichern fehlgeschlagen: {e}")
            
            print(f"   💾 State gespeichert (Step {step})")

            if platform is not None:
                try:
                    if await platform.is_completed(page):
                        print("   ✅ Plattform meldet Survey-Completion")
                        break
                except Exception:
                    pass
        else:
            # Survey wahrscheinlich beendet oder Fehler
            if steps_completed == 0:
                print("   ℹ️  Survey konnte nicht gestartet werden")
            else:
                print(f"   ℹ️  Survey nach {steps_completed} Schritten beendet")
            break
    
    # Finaler State
    try:
        await save_browser_state(context, session_id)
        save_cli_state(session_id, {
            "step_index": steps_completed,
            "url": page.url,
            "timestamp": time.time(),
            "status": "completed" if steps_completed > 0 else "failed"
        })
        print("💾 Finaler State gespeichert")
    except Exception as e:
        print(f"⚠️  Finaler State speichern fehlgeschlagen: {e}")
    
    return {
        "success": steps_completed > 0,
        "session_id": session_id,
        "steps_completed": steps_completed,
        "final_url": page.url
    }


async def resume_survey_flow(
    page: Page,
    context: BrowserContext,
    session_id: str,
    max_steps: int = 10,
    strategy_name: str = "persona",
    strategy_persona: str = "neutral",
) -> Dict[str, Any]:
    """
    Setzt eine unterbrochene Survey-Session fort.
    
    Returns:
        Dictionary mit Ergebnissen und Status
    """
    print(f"🔄 Resume Survey (Session: {session_id})")
    
    try:
        cli_state = load_cli_state(session_id)
        start_step = cli_state.get("step_index", 0) + 1
        previous_url = cli_state.get("url")
        
        print(f"   ℹ️  Vorheriger Stand: Step {start_step - 1}, URL: {previous_url}")
        
        if previous_url:
            await page.goto(previous_url, wait_until="domcontentloaded", timeout=30000)
            await medium_delay()
        
        # Continue mit normalem Flow ab nächstem Step
        result = await execute_survey_flow(
            page, context, 
            start_url=previous_url or "",
            max_steps=max_steps,
            session_id=session_id,
            strategy_name=strategy_name,
            strategy_persona=strategy_persona,
        )
        
        result["resumed"] = True
        result["resumed_from_step"] = start_step - 1
        return result
        
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Kein State gefunden für Session: {session_id}",
            "session_id": session_id
        }
