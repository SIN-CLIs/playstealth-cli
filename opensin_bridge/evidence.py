# ================================================================================
# DATEI: evidence.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Evidence bundle helper -- issue #70 (worker side).

Calls ``bridge.evidenceBundle`` on the Chrome extension and writes the
result to ``reports/evidence/<trace_id>.json`` with a matching
``.screenshot.b64`` sidecar when present.
"""

from __future__ import annotations

import base64
import json
import pathlib
from dataclasses import dataclass
from typing import Any

from opensin_bridge.adapter import BridgeAdapter


@dataclass
class EvidenceBundle:
    # ========================================================================
    # KLASSE: EvidenceBundle
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    trace_id: str
    path: pathlib.Path
    payload: dict[str, Any]

    def screenshot_bytes(self) -> bytes | None:
        b64 = self.payload.get("screenshot")
        return base64.b64decode(b64) if b64 else None


async def capture(
    bridge: BridgeAdapter,
    *,
    tab_id: int | None = None,
    trace_id: str | None = None,
    out_dir: str = "reports/evidence",
    include_screenshot: bool = True,
) -> EvidenceBundle:
    payload: dict[str, Any] = await bridge(
        "bridge.evidenceBundle",
        tabId=tab_id,
        traceId=trace_id,
        includeScreenshot=include_screenshot,
    )
    tid = payload.get("traceId") or trace_id or payload.get("ts") or "unknown"
    root = pathlib.Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{tid}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    if include_screenshot and payload.get("screenshot"):
        (root / f"{tid}.screenshot.b64").write_text(payload["screenshot"])
    return EvidenceBundle(trace_id=str(tid), path=path, payload=payload)
