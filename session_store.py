#!/usr/bin/env python3
# ================================================================================
# DATEI: session_store.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

# -*- coding: utf-8 -*-
"""
================================================================================
Session Store — Persistente Cookies / LocalStorage / SessionStorage ueber Runs
================================================================================
WHY: Panel-Logins (HeyPiggy, PureSpectrum, Dynata, Sapio) kosten viel Zeit:
     2FA, Captcha, Email-Validation, Screener vor dem eigentlichen Screener.
     Jedes Mal neu durchlaufen zu muessen ist Zeit- und Trust-Verlust —
     Panels markieren haeufige Neu-Logins als verdaechtig.
CONSEQUENCES: Wir dumpen nach jedem erfolgreichen Login / Dashboard-Load
     die komplette Web-Storage-Schicht (Cookies + localStorage + sessionStorage)
     in eine persistente Datei ausserhalb des Run-Verzeichnisses
     (~/.heypiggy/session_cache.json). Beim naechsten Start stellen wir die
     Session BEVOR wir die erste Seite laden wieder her.

FORMAT:
  {
    "saved_at": "2026-04-18T12:34:56Z",
    "domains": {
      "heypiggy.com": {
        "cookies": [...],
        "localStorage": {"key": "value", ...},
        "sessionStorage": {"key": "value", ...}
      },
      "purespectrum.io": { ... }
    }
  }

SICHERHEIT:
  - Die Datei enthaelt Session-Tokens. Wir setzen chmod 600 auf POSIX.
  - Windows: ACL wird nicht explizit gesetzt, Nutzer-Home ist schon privat.
  - Wer das Worker-File liest kann sich als der User ausgeben. Das ist so
    gewuenscht — wir wollen ja exakt diese Identitaet wiederherstellen.
================================================================================
"""

from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

# Default-Location fuer den Session-Cache (persistent ueber Runs hinweg).
# Override via HEYPIGGY_SESSION_CACHE env variable.
DEFAULT_CACHE_PATH: Path = Path(
    os.environ.get(
        "HEYPIGGY_SESSION_CACHE",
        str(Path.home() / ".heypiggy" / "session_cache.json"),
    )
)

# Domains fuer die wir Storage mitnehmen wollen. Alles andere ist Noise.
# Erweiterbar per HEYPIGGY_SESSION_DOMAINS (Komma-Liste).
DEFAULT_DOMAINS: list[str] = [
    "heypiggy.com",
    "purespectrum.io",
    "pspmarket.com",
    "dynata.com",
    "researchnow.com",
    "samplicio.us",
    "sapioresearch.com",
    "cint.com",
    "lucidhq.com",
    "lucidholdings.com",
]

BridgeFn = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _domains_from_env() -> list[str]:
    extra = os.environ.get("HEYPIGGY_SESSION_DOMAINS", "").strip()
    if not extra:
        return DEFAULT_DOMAINS
    merged = {d.lower() for d in DEFAULT_DOMAINS}
    for d in extra.split(","):
        d = d.strip().lower()
        if d:
            merged.add(d)
    return sorted(merged)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _secure_chmod(path: Path) -> None:
    """chmod 600 auf POSIX-Systemen, silent no-op auf Windows."""
    try:
        if os.name == "posix":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _host_to_domain(host: str) -> str:
    """'login.heypiggy.com' -> 'heypiggy.com' (simple 2-Level-TLD-Heuristik)."""
    host = (host or "").lstrip(".").lower()
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Spezialfall: co.uk / com.au -> 3 Segmente. Wir nehmen konservativ 2.
    return ".".join(parts[-2:])


def _cookie_domain_matches(cookie_domain: str, target_domain: str) -> bool:
    cd = (cookie_domain or "").lstrip(".").lower()
    return cd == target_domain or cd.endswith("." + target_domain)


# ----------------------------------------------------------------------------
# DUMP
# ----------------------------------------------------------------------------


async def dump_session(
    execute_bridge: BridgeFn,
    tab_params: dict[str, Any],
    cache_path: Path | None = None,
    audit: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """
    Exportiert Cookies + localStorage + sessionStorage der aktuellen Tab-Session
    und merged sie in den persistenten Cache.

    Returns: Dict mit {written: int, domains: [..], path: str}.
    """
    audit = audit or (lambda *a, **kw: None)
    path = cache_path or DEFAULT_CACHE_PATH
    domains = _domains_from_env()

    # 1) Alle Cookies von der Bridge holen
    cookies: list[dict[str, Any]] = []
    try:
        raw = await execute_bridge("export_all_cookies", tab_params)
        if isinstance(raw, list):
            cookies = raw
        elif isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
            cookies = raw["cookies"]
    except Exception as e:
        audit("session_dump_cookies_error", error=str(e))

    # 2) LocalStorage + SessionStorage via JS dumpen
    storage_js = r"""
    (function() {
      function dump(s) {
        var out = {};
        try {
          for (var i = 0; i < s.length; i++) {
            var k = s.key(i);
            if (!k) continue;
            try { out[k] = s.getItem(k); } catch(e) {}
          }
        } catch(e) {}
        return out;
      }
      return {
        host: location.hostname,
        origin: location.origin,
        local: dump(window.localStorage || {length:0, key:function(){return null}, getItem:function(){return null}}),
        session: dump(window.sessionStorage || {length:0, key:function(){return null}, getItem:function(){return null}})
      };
    })();
    """
    local_storage: dict[str, str] = {}
    session_storage: dict[str, str] = {}
    current_host = ""
    try:
        js_res = await execute_bridge(
            "execute_javascript", {"script": storage_js, **tab_params}
        )
        payload = js_res.get("result") if isinstance(js_res, dict) else None
        if isinstance(payload, dict):
            current_host = str(payload.get("host", ""))
            ls = payload.get("local") or {}
            ss = payload.get("session") or {}
            if isinstance(ls, dict):
                local_storage = {
                    str(k): str(v) for k, v in ls.items() if isinstance(k, str)
                }
            if isinstance(ss, dict):
                session_storage = {
                    str(k): str(v) for k, v in ss.items() if isinstance(k, str)
                }
    except Exception as e:
        audit("session_dump_storage_error", error=str(e))

    # 3) Vorhandenen Cache laden (Merge statt Replace pro Domain)
    existing: dict[str, Any] = {"saved_at": _now_iso(), "domains": {}}
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if "domains" not in existing or not isinstance(existing["domains"], dict):
                existing["domains"] = {}
    except Exception as e:
        audit("session_dump_cache_read_error", error=str(e))

    # 4) Pro Ziel-Domain: die relevanten Cookies + Storage extrahieren & mergen
    current_domain = _host_to_domain(current_host)
    written_domains: list[str] = []
    for target in domains:
        domain_cookies = [
            c for c in cookies
            if isinstance(c, dict)
            and _cookie_domain_matches(str(c.get("domain", "")), target)
        ]
        # Storage nur mitnehmen wenn die aktuelle Seite zu dieser Domain passt
        dom_local = local_storage if current_domain == target else None
        dom_session = session_storage if current_domain == target else None

        if not domain_cookies and dom_local is None and dom_session is None:
            continue

        prior = existing["domains"].get(target, {}) if isinstance(
            existing.get("domains"), dict
        ) else {}

        merged_entry: dict[str, Any] = dict(prior)
        if domain_cookies:
            merged_entry["cookies"] = domain_cookies
            merged_entry["cookies_saved_at"] = _now_iso()
        if dom_local is not None:
            merged_entry["localStorage"] = dom_local
            merged_entry["localStorage_saved_at"] = _now_iso()
        if dom_session is not None:
            merged_entry["sessionStorage"] = dom_session
            merged_entry["sessionStorage_saved_at"] = _now_iso()

        existing["domains"][target] = merged_entry
        written_domains.append(target)

    existing["saved_at"] = _now_iso()

    # 5) Atomar schreiben + chmod 600
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        _secure_chmod(path)
        audit(
            "session_dump_ok",
            path=str(path),
            domains=written_domains,
            cookie_count=len(cookies),
        )
    except Exception as e:
        audit("session_dump_write_error", error=str(e))
        return {"written": 0, "domains": [], "path": str(path), "error": str(e)}

    return {
        "written": len(written_domains),
        "domains": written_domains,
        "path": str(path),
    }


# ----------------------------------------------------------------------------
# RESTORE
# ----------------------------------------------------------------------------


async def restore_session(
    execute_bridge: BridgeFn,
    tab_params: dict[str, Any],
    target_url: str,
    cache_path: Path | None = None,
    audit: Callable[..., None] | None = None,
    max_age_hours: int = 72,
) -> dict[str, Any]:
    """
    Stellt eine zuvor gespeicherte Session fuer die Domain von target_url wieder
    her: Cookies werden in den Browser injiziert, localStorage +
    sessionStorage nachgefuellt.

    MUSS AUFGERUFEN WERDEN: NACHDEM der Tab offen ist und auf einer Seite der
    Ziel-Domain steht (sonst kann Storage nicht gesetzt werden), aber VOR dem
    ersten Login-Versuch.

    Returns: {restored: bool, reason: str, cookies_set: int, storage_keys: int}
    """
    audit = audit or (lambda *a, **kw: None)
    path = cache_path or DEFAULT_CACHE_PATH

    if not path.exists():
        return {"restored": False, "reason": "no_cache_file"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        audit("session_restore_parse_error", error=str(e))
        return {"restored": False, "reason": f"parse_error:{e}"}

    saved_at = str(data.get("saved_at", ""))
    try:
        if saved_at:
            ts = datetime.strptime(saved_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h > max_age_hours:
                audit("session_restore_stale", age_hours=round(age_h, 1))
                return {"restored": False, "reason": f"stale_{int(age_h)}h"}
    except Exception:
        pass

    try:
        host = urlparse(target_url).hostname or ""
        target_domain = _host_to_domain(host)
    except Exception:
        target_domain = ""

    if not target_domain:
        return {"restored": False, "reason": "no_target_domain"}

    domains = data.get("domains", {}) or {}
    entry = domains.get(target_domain)
    if not entry:
        audit("session_restore_no_entry", target=target_domain)
        return {"restored": False, "reason": "no_entry_for_domain"}

    # 1) Cookies injizieren
    cookies = entry.get("cookies") or []
    cookies_set = 0
    for c in cookies:
        if not isinstance(c, dict):
            continue
        try:
            await execute_bridge(
                "set_cookie",
                {**tab_params, "cookie": c},
            )
            cookies_set += 1
        except Exception as e:
            # Einzelne Cookies koennen vom Browser abgelehnt werden
            # (Domain-Missmatch, HttpOnly-Beschraenkungen) — nicht fatal.
            audit("session_restore_cookie_reject", name=c.get("name", "?"), error=str(e)[:80])

    # 2) LocalStorage + SessionStorage per JS schreiben
    local = entry.get("localStorage") or {}
    sess = entry.get("sessionStorage") or {}
    storage_keys = 0
    if local or sess:
        # JSON.stringify-safe via direkte Argumente statt inline-Interpolation
        inject_js = r"""
        (function(localData, sessionData) {
          var set = 0;
          try {
            for (var k in localData) {
              if (Object.prototype.hasOwnProperty.call(localData, k)) {
                try { window.localStorage.setItem(k, localData[k]); set++; } catch(e) {}
              }
            }
          } catch(e) {}
          try {
            for (var k2 in sessionData) {
              if (Object.prototype.hasOwnProperty.call(sessionData, k2)) {
                try { window.sessionStorage.setItem(k2, sessionData[k2]); set++; } catch(e) {}
              }
            }
          } catch(e) {}
          return set;
        })(%s, %s);
        """ % (json.dumps(local), json.dumps(sess))
        try:
            res = await execute_bridge(
                "execute_javascript", {"script": inject_js, **tab_params}
            )
            if isinstance(res, dict):
                storage_keys = int(res.get("result") or 0)
        except Exception as e:
            audit("session_restore_storage_error", error=str(e))

    audit(
        "session_restore_ok",
        target=target_domain,
        cookies_set=cookies_set,
        cookies_total=len(cookies),
        storage_keys=storage_keys,
        saved_at=saved_at,
    )
    return {
        "restored": True,
        "reason": "ok",
        "cookies_set": cookies_set,
        "storage_keys": storage_keys,
        "saved_at": saved_at,
    }


# ----------------------------------------------------------------------------
# CLI (Debug-Helfer)
# ----------------------------------------------------------------------------


def inspect_cache(cache_path: Path | None = None) -> dict[str, Any]:
    """Liefert eine lesbare Uebersicht was im Cache steht (ohne Secret-Werte)."""
    path = cache_path or DEFAULT_CACHE_PATH
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"exists": True, "path": str(path), "error": str(e)}
    summary: dict[str, Any] = {
        "exists": True,
        "path": str(path),
        "saved_at": data.get("saved_at"),
        "domains": {},
    }
    for d, entry in (data.get("domains") or {}).items():
        summary["domains"][d] = {
            "cookies": len(entry.get("cookies") or []),
            "localStorage_keys": len(entry.get("localStorage") or {}),
            "sessionStorage_keys": len(entry.get("sessionStorage") or {}),
            "cookies_saved_at": entry.get("cookies_saved_at"),
        }
    return summary


if __name__ == "__main__":
    import pprint

    pprint.pprint(inspect_cache())
