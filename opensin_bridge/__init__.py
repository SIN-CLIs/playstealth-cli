# ================================================================================
# DATEI: __init__.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""OpenSIN Bridge client -- issue #69.

Python counterpart to ``extension/src/contract/v1/index.js`` in the
OpenSIN-Bridge repo. Validates method names, normalises errors, enforces
retry semantics, and emits observability events.
"""

from opensin_bridge.contract import (
    BRIDGE_CONTRACT_VERSION,
    BridgeError,
    BridgeMethod,
    ContractMismatch,
    ERROR_CODES,
    METHODS,
    classify_error,
    is_idempotent,
    retry_hint_for,
)
from opensin_bridge.adapter import BridgeAdapter, BridgeCallResult

__all__ = [
    "BRIDGE_CONTRACT_VERSION",
    "BridgeAdapter",
    "BridgeCallResult",
    "BridgeError",
    "BridgeMethod",
    "ContractMismatch",
    "ERROR_CODES",
    "METHODS",
    "classify_error",
    "is_idempotent",
    "retry_hint_for",
]
