# ================================================================================
# DATEI: __main__.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Entry point for ``python -m worker``.

Delegates to :func:`worker.cli.main` so that both of these work identically::

    python -m worker
    heypiggy-worker        # via the console_scripts entry point
"""

from __future__ import annotations

from worker.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
