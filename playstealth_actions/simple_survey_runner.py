"""
Einfacher Survey Runner - Fokus auf Zuverlässigkeit statt Komplexität.

Verwendet:
- simple_selector.py für robuste Element-Lokalisierung
- human_delay.py für menschliche Pausen
- state_store.py für vollständige Session-Persistenz
"""
import asyncio
import time
from typing import Optional, Dict, Any
from playwright.async_api import Page, BrowserContext

from .simple_selector import find_element, safe_click, safe_fill
from .human_delay import fast_delay, medium_delay, slow_delay
from .state_store import save_cli_state, save_browser_state, load_cli_state, list_sessions
from .telemetry import log_event, generate_session_id


async def run_survey_step(
    page: Page,
    step_index: int,
    context: Optional[BrowserContext] = None,
    session_id: Optional[str] = None
) -> bool:
    """
    Führt einen einzelnen Survey-Schritt aus mit robusten Selektoren und Delays.
    
    Returns:
        True wenn erfolgreich, False wenn fehlgeschlagen oder Ende erreicht
    """
    start_time = time.perf_counter()
    
    try:
        # Prüfen ob Survey noch existiert
        page_content = await page.content()
        if "survey" not in page_content.lower() and "question" not in page_content.lower():
            print(f"   ℹ️  Step {step_index}: Keine Survey-Elemente erkannt - möglicherweise beendet")
            return False
        
        # Typische Survey-Elemente finden und interagieren
        # Priorität: Weiter-Buttons → Antwort-Optionen → Input-Felder
        
        # 1. Nach "Weiter" / "Next" Buttons suchen
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
                    action=f"clicked_{btn_text}"
                )
                return True
        
        # 2. Nach Radio-Buttons / Checkboxen suchen (Antwort-Optionen)
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
                    import random
                    option_index = random.randint(0, min(count - 1, 2))  # Max Index 2
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
                                action=f"answered_and_clicked_{btn_text}"
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
                        action=f"answered_{selector}"
                    )
                    return True
            except Exception:
                continue
        
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
                        action=f"filled_{selector}"
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
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Haupt-Survey-Flow mit State-Persistenz nach jedem Schritt.
    
    Returns:
        Dictionary mit Ergebnissen und Status
    """
    if session_id is None:
        session_id = generate_session_id()
    
    print(f"🚀 Starte Survey-Flow (Session: {session_id}, Max Steps: {max_steps})")
    
    # Initiale Navigation
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
        await medium_delay()
        print(f"   ✓ Seite geladen: {start_url}")
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
        
        success = await run_survey_step(page, step, context, session_id)
        
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
        else:
            # Survey wahrscheinlich beendet oder Fehler
            if steps_completed == 0:
                print(f"   ℹ️  Survey konnte nicht gestartet werden")
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
        print(f"💾 Finaler State gespeichert")
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
    max_steps: int = 10
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
            session_id=session_id
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
