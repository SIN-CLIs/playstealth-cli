# ================================================================================
# DATEI: observability.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Structured trace sink that plays nicely with ``observability.py``.

Ties together ``BridgeAdapter`` traces, runtime state transitions, and
evidence bundles. Consumers get a single append-only JSONL file under
``reports/traces/<session_id>.jsonl``.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
import uuid
from typing import Any


class TraceRecorder:
    def __init__(self, *, session_id: str | None = None, out_dir: str = "reports/traces") -> None:
        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:12]}"
        self._path = pathlib.Path(out_dir) / f"{self.session_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        event = {"session_id": self.session_id, "ts": time.time(), **event}
        with self._lock:
            with self._path.open("a") as fh:
                fh.write(json.dumps(event, default=str) + "\n")

    @property
    def path(self) -> pathlib.Path:
        return self._path
