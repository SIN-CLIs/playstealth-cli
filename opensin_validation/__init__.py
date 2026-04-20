# ================================================================================
# DATEI: __init__.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Validation harness -- issue #76."""
from opensin_validation.harness import ValidationReport, ValidationHarness

__all__ = ["ValidationReport", "ValidationHarness"]
