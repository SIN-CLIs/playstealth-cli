# ================================================================================
# DATEI: __init__.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Stealth strategy registry -- issue #74."""
from opensin_stealth.strategy import (
    StealthStrategy,
    PassiveStrategy,
    HumanizedStrategy,
    register,
    registry,
    select,
)

__all__ = [
    "StealthStrategy",
    "PassiveStrategy",
    "HumanizedStrategy",
    "register",
    "registry",
    "select",
]
