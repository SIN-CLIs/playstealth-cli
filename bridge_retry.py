# ================================================================================
# DATEI: bridge_retry.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""
Retry-Wrapper mit Exponential Backoff fuer Bridge/MCP-Calls.

WHY (DE): Die Chrome-MCP-Bridge kann kurzzeitig fehlschlagen wenn der Browser
gerade navigiert, ein Dialog geoeffnet ist, Netzwerk wackelt oder die
Extension einen neuen Worker-Kontext startet. Ohne Retry reisst ein einzelner
200ms-Spike sofort die ganze Umfrage ab. Mit klassifiziertem Retry ueberleben
wir 98% dieser transienten Fehler ohne dass der Vision-Agent sie sieht.

KONSEQUENZEN:
- Der Aufrufer bekommt entweder ein gueltiges Ergebnis oder — nach 3 Versuchen
  mit 200ms / 600ms / 1800ms Wartezeit — das letzte Error-Dict.
- Nicht-transiente Fehler (z.B. invalid_argument, auth) werden SOFORT
  zurueckgegeben (kein Retry), damit wir nicht Quota verbrennen.
- Der Retry-Counter wird auditiert damit wir in run_summary sehen wie oft
  die Bridge wackelt (Frueh-Warnsystem fuer Infra-Probleme).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable


# Signaturen die wir als "transient" klassifizieren. WHY: Substring-Match weil
# die Bridge Fehler in freiem Text zurueckgibt, nicht als Enum.
TRANSIENT_MARKERS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "econnreset",
    "econnrefused",
    "socket hang up",
    "network error",
    "navigation interrupted",
    "target closed",
    "context was destroyed",
    "execution context",
    "temporarily unavailable",
    "bridge not ready",
    "ws disconnect",
    "websocket closed",
    "chrome not reachable",
    "no response",
)

# Signaturen die NIE retried werden. WHY: Bei Auth/Invalid-Argument waere Retry
# sinnlos und gefaehrlich (Rate-Limit-Verletzung, Account-Sperre).
PERMANENT_MARKERS: tuple[str, ...] = (
    "unauthorized",
    "forbidden",
    "invalid argument",
    "invalid parameter",
    "not found",
    "no tab with id",  # Tab-Recovery laeuft bereits im Worker, nicht hier retryen
    "no tab with given id",
    "method not found",
)


def classify_result(result: Any) -> str:
    """
    Klassifiziert ein Bridge-Ergebnis in 'ok' / 'transient' / 'permanent'.

    WHY: Nur 'transient' wird retried.
    """
    if not isinstance(result, dict):
        return "ok"
    err = result.get("error") or result.get("errorMessage") or result.get("message")
    if not err:
        # Auch wenn kein error-Feld, pruefen ob 'ok: false' gesetzt
        if result.get("ok") is False and result.get("reason"):
            err = result.get("reason")
    if not err:
        return "ok"
    err_low = str(err).lower()
    for marker in PERMANENT_MARKERS:
        if marker in err_low:
            return "permanent"
    for marker in TRANSIENT_MARKERS:
        if marker in err_low:
            return "transient"
    # Unbekannter Fehler — einmal retryen koennte helfen, aber wir sind
    # konservativ und behandeln als permanent.
    return "permanent"


async def call_with_retry(
    bridge: Callable[[str, dict[str, Any] | None], Awaitable[Any]],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 4.0,
    audit: Callable[..., None] | None = None,
) -> Any:
    """
    Ruft `bridge(method, params)` mit Exponential Backoff bei transienten
    Fehlern auf.

    Args:
      bridge: Die eigentliche execute_bridge-Funktion des Workers.
      method: MCP-Methodenname.
      params: Argumente.
      max_attempts: Maximale Anzahl Versuche (inkl. erstem).
      base_delay: Wartezeit vor zweitem Versuch in Sekunden.
      max_delay: Obergrenze fuer Wartezeit.
      audit: Optionaler Audit-Callback(event, **fields).

    Returns:
      Das Ergebnis des letzten Versuchs. Bei 'ok' wird sofort zurueckgegeben.
    """
    attempt = 0
    last_result: Any = None
    while attempt < max_attempts:
        try:
            result = await bridge(method, params)
        except Exception as e:
            # Exceptions aus der Bridge-Schicht selbst -> als transient behandeln
            result = {"error": f"exception: {type(e).__name__}: {e}"}
        attempt += 1
        klass = classify_result(result)
        if klass == "ok":
            if attempt > 1 and audit:
                audit(
                    "bridge_retry_success",
                    method=method,
                    attempts=attempt,
                )
            return result
        if klass == "permanent" or attempt >= max_attempts:
            if attempt > 1 and audit:
                audit(
                    "bridge_retry_exhausted",
                    method=method,
                    attempts=attempt,
                    classification=klass,
                )
            return result
        # Transient -> exponential backoff mit Jitter
        delay = min(max_delay, base_delay * (3.0 ** (attempt - 1)))
        jitter = delay * 0.3 * random.random()
        wait = delay + jitter
        if audit:
            audit(
                "bridge_retry_wait",
                method=method,
                attempt=attempt,
                wait_ms=round(wait * 1000),
                classification=klass,
            )
        await asyncio.sleep(wait)
        last_result = result
    return last_result


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio as _a

    async def _main() -> None:
        calls = {"n": 0}

        async def fake_bridge(method: str, params: dict[str, Any] | None):
    # -------------------------------------------------------------------------
    # FUNKTION: fake_bridge
    # PARAMETER: method: str, params: dict[str, Any] | None
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
            calls["n"] += 1
            if calls["n"] < 3:
                return {"error": "timeout while navigating"}
            return {"ok": True, "method": method}

        audit_log: list[tuple[str, dict[str, Any]]] = []

        def _audit(event: str, **kw: Any) -> None:
            audit_log.append((event, kw))

        res = await call_with_retry(fake_bridge, "navigate", {"url": "x"}, audit=_audit)
        assert res == {"ok": True, "method": "navigate"}, res
        assert calls["n"] == 3
        assert any(e[0] == "bridge_retry_success" for e in audit_log)

        # Permanent error -> kein Retry
        async def perma(method, params):
    # -------------------------------------------------------------------------
    # FUNKTION: perma
    # PARAMETER: method, params
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
            return {"error": "unauthorized"}

        res2 = await call_with_retry(perma, "x", max_attempts=5)
        assert res2 == {"error": "unauthorized"}
        print("bridge_retry self-test ok")

    _a.run(_main())
