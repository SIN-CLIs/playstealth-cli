"""Pacing Controller for human-like rhythms and session locking."""
import asyncio
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

LOCK_FILE = Path(__import__("os").getenv("PLAYSTEALTH_STATE_DIR", ".playstealth_state")) / ".session.lock"

def acquire_session_lock() -> bool:
    """Verhindert parallele Survey-Runs."""
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 3600:  # Lock verfällt nach 1h (Crash-Safety)
            return False
    LOCK_FILE.touch()
    return True

def release_session_lock():
    """Release session lock."""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()

async def human_reading_delay(question_text: str, wpm: int = 220):
    """Simuliert Lesezeit basierend auf Wortanzahl."""
    words = len(question_text.split())
    base_sec = (words / wpm) * 60
    delay = max(1.5, base_sec + random.uniform(-0.8, 1.2))
    await asyncio.sleep(delay)

async def inter_survey_break(min_min: float = 5.0, max_min: float = 25.0):
    """Menschliche Pause zwischen Surveys."""
    mins = random.uniform(min_min, max_min)
    print(f"☕ Taking human break: {mins:.1f} min...")
    await asyncio.sleep(mins * 60)

def is_within_active_hours(start_h: int = 8, end_h: int = 22) -> bool:
    """Circadianer Rhythmus: Keine 24/7-Aktivität.

    For local debugging or controlled operator runs we allow an explicit env
    override so the CLI can still be tested outside normal active hours.
    """
    if os.getenv("PLAYSTEALTH_IGNORE_ACTIVE_HOURS", "").lower() in {"1", "true", "yes"}:
        return True
    return start_h <= datetime.now().hour < end_h
