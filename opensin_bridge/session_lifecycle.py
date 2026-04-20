# ================================================================================
# DATEI: session_lifecycle.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

"""Worker-side session lifecycle helpers -- issue #71.

Builds on the bridge's session tools. The worker never touches cookies
directly; it asks the bridge for a manifest and checks health/last-known-
good before taking actions that assume auth state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opensin_bridge.adapter import BridgeAdapter


@dataclass
class SessionManifest:
    origin: str
    ttl_seconds: int
    created_at: float
    expires_at: float
    source: str
    status: str
    note: str = ""
    tab_id: int | None = None
    last_known_good: dict[str, Any] | None = None

    @classmethod
    def from_wire(cls, payload: dict[str, Any]) -> "SessionManifest":
        return cls(
            origin=payload.get("origin", ""),
            ttl_seconds=int(payload.get("ttlSeconds", 0)),
            created_at=float(payload.get("createdAt", 0)) / 1000.0,
            expires_at=float(payload.get("expiresAt", 0)) / 1000.0,
            source=payload.get("source", "runtime"),
            status=payload.get("status", "active"),
            note=payload.get("note", ""),
            tab_id=payload.get("tabId"),
            last_known_good=payload.get("lastKnownGood"),
        )


async def get_or_refresh(
    bridge: BridgeAdapter,
    *,
    origin: str,
    tab_id: int | None = None,
    ttl_seconds: int | None = None,
    source: str = "runtime",
    note: str = "",
) -> SessionManifest:
    payload = await bridge(
        "session.manifest",
        origin=origin,
        tabId=tab_id,
        ttlSeconds=ttl_seconds,
        source=source,
        note=note,
    )
    return SessionManifest.from_wire(payload["manifest"])


async def health(bridge: BridgeAdapter, *, origin: str) -> dict[str, Any]:
    payload = await bridge("session.health", origin=origin)
    return payload["health"]


async def last_known_good(bridge: BridgeAdapter, *, origin: str) -> SessionManifest | None:
    payload = await bridge("session.lastKnownGood", origin=origin)
    lkg = payload.get("manifest")
    return SessionManifest.from_wire(lkg) if lkg else None


async def invalidate(bridge: BridgeAdapter, *, origin: str, reason: str) -> None:
    await bridge("session.invalidate", origin=origin, reason=reason)
