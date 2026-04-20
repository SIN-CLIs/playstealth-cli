# ================================================================================
# DATEI: bridge_contract.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from worker.exceptions import BridgeProtocolError, BridgeUnavailableError
from worker.logging import get_logger

_log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class BridgeRequest:
    # ========================================================================
    # KLASSE: BridgeRequest
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    method: str
    params: dict[str, object] = field(default_factory=dict)
    page_fingerprint: str = ""
    timeout_seconds: int = 30
    request_id: int = 1

    def to_jsonrpc_body(self) -> dict[str, object]:
        body: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": self.method,
            "id": self.request_id,
            "meta": {"page_fingerprint": self.page_fingerprint},
        }
        if self.params:
            body["params"] = self.params
        return body


@dataclass(slots=True, frozen=True)
class BridgeResponse:
    # ========================================================================
    # KLASSE: BridgeResponse
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    ok: bool
    result: object
    error: str = ""
    status_code: int = 200
    attempt_count: int = 1


def call_bridge_with_retry(base_url: str, request: BridgeRequest) -> BridgeResponse:
    delays = (1, 2, 4)
    last_error = ""
    for attempt, delay in enumerate(delays, start=1):
        try:
            http_request = urllib.request.Request(
                base_url,
                data=json.dumps(request.to_jsonrpc_body()).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(http_request, timeout=request.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise BridgeProtocolError("bridge payload must be object")
                if "error" in payload:
                    raise BridgeProtocolError(f"bridge protocol error: {payload['error']}")
                return BridgeResponse(
                    ok=True,
                    result=payload.get("result", {}),
                    status_code=resp.getcode(),
                    attempt_count=attempt,
                )
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise BridgeUnavailableError(
                    "bridge returned caller error",
                    status_code=exc.code,
                    method=request.method,
                ) from exc
            last_error = f"HTTP {exc.code}: {exc.reason}"
        except (urllib.error.URLError, TimeoutError, BridgeProtocolError) as exc:
            last_error = str(exc)

        _log.warning(
            "bridge_retry",
            method=request.method,
            attempt=attempt,
            delay_seconds=delay,
            error=last_error,
        )
        if attempt < len(delays):
            time.sleep(delay)

    raise BridgeUnavailableError(
        "bridge failed after retries",
        method=request.method,
        error=last_error,
    )


__all__ = ["BridgeRequest", "BridgeResponse", "call_bridge_with_retry"]
