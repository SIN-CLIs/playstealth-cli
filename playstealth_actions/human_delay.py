"""Einfache menschliche Delays - keine komplexen Kurven, nur Zufall."""
import asyncio
import random


async def human_delay(mean: float = 0.6, std: float = 0.25) -> None:
    """
    Einfache, menschliche Pause zwischen Aktionen.
    
    Args:
        mean: Durchschnittliche Verzögerung in Sekunden (default: 0.6s)
        std: Standardabweichung (default: 0.25s)
        
    Die Verzögerung wird auf minimum 0.2s begrenzt, um nicht zu schnell zu sein.
    """
    delay = max(0.2, random.gauss(mean, std))
    await asyncio.sleep(delay)


async def fast_delay() -> None:
    """Kurze Pause für schnelle Interaktionen (0.3-0.8s)."""
    await human_delay(mean=0.5, std=0.15)


async def medium_delay() -> None:
    """Mittlere Pause für normale Interaktionen (0.5-1.2s)."""
    await human_delay(mean=0.8, std=0.2)


async def slow_delay() -> None:
    """Längere Pause für nachdenkliche Momente (1.0-2.0s)."""
    await human_delay(mean=1.5, std=0.3)


async def thinking_delay() -> None:
    """Denk-Pause vor wichtigen Entscheidungen (2-4s)."""
    await human_delay(mean=3.0, std=0.5)
