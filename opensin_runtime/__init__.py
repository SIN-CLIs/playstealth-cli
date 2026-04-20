# ================================================================================
# DATEI: __init__.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Runtime state machine -- issue #72."""
from opensin_runtime.state_machine import (
    RuntimeState,
    StateMachine,
    StateTransition,
    IllegalTransition,
)

__all__ = ["RuntimeState", "StateMachine", "StateTransition", "IllegalTransition"]
