# ================================================================================
# DATEI: sitepack.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker.exceptions import SelectorNotFoundError, SitepackValidationError
from worker.logging import get_logger

_log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class Sitepack:
    # ========================================================================
    # KLASSE: Sitepack
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    site: str
    version: str
    selectors: dict[str, str]
    flows: dict[str, list[str]]
    page_signatures: dict[str, list[str]]


class SitepackLoader:
    # ========================================================================
    # KLASSE: SitepackLoader
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def __init__(self) -> None:
        self._pack: Sitepack | None = None

    def load(self, path: str | Path) -> Sitepack:
        file_path = Path(path)
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SitepackValidationError(
                "failed to read sitepack", path=str(file_path), cause=str(exc)
            ) from exc
        pack = _validate_sitepack(payload)
        self._pack = pack
        _log.info("sitepack_loaded", site=pack.site, version=pack.version, path=str(file_path))
        return pack

    def get_selector(self, name: str) -> str:
        pack = self._require_pack()
        try:
            return pack.selectors[name]
        except KeyError as exc:
            raise SelectorNotFoundError("unknown sitepack selector", selector=name) from exc

    def get_flow(self, name: str) -> list[str]:
        pack = self._require_pack()
        try:
            return pack.flows[name]
        except KeyError as exc:
            raise SitepackValidationError("unknown sitepack flow", flow=name) from exc

    def get_page_signature(self, name: str) -> list[str]:
        pack = self._require_pack()
        try:
            return pack.page_signatures[name]
        except KeyError as exc:
            raise SitepackValidationError("unknown sitepack page signature", page=name) from exc

    def _require_pack(self) -> Sitepack:
        if self._pack is None:
            raise SitepackValidationError("sitepack has not been loaded yet")
        return self._pack


def _validate_sitepack(payload: Any) -> Sitepack:
    if not isinstance(payload, dict):
        raise SitepackValidationError("sitepack payload must be a JSON object")
    site = payload.get("site")
    version = payload.get("version")
    selectors = payload.get("selectors")
    flows = payload.get("flows")
    page_signatures = payload.get("page_signatures")
    if not isinstance(site, str) or not site:
        raise SitepackValidationError("sitepack missing non-empty 'site'")
    if not isinstance(version, str) or not version:
        raise SitepackValidationError("sitepack missing non-empty 'version'")
    if not isinstance(selectors, dict) or not selectors:
        raise SitepackValidationError("sitepack missing non-empty 'selectors'")
    if not isinstance(flows, dict):
        raise SitepackValidationError("sitepack missing 'flows'")
    if not isinstance(page_signatures, dict):
        raise SitepackValidationError("sitepack missing 'page_signatures'")

    selector_map = _coerce_string_map(selectors, field="selectors")
    flow_map = _coerce_list_map(flows, field="flows")
    signature_map = _coerce_list_map(page_signatures, field="page_signatures")
    return Sitepack(
        site=site,
        version=version,
        selectors=selector_map,
        flows=flow_map,
        page_signatures=signature_map,
    )


def _coerce_string_map(raw: dict[str, Any], *, field: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value:
            raise SitepackValidationError(
                f"sitepack field '{field}.{key}' must be a non-empty string"
            )
        out[str(key)] = value
    return out


def _coerce_list_map(raw: dict[str, Any], *, field: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise SitepackValidationError(
                f"sitepack field '{field}.{key}' must be a non-empty string list"
            )
        out[str(key)] = list(value)
    return out


__all__ = ["Sitepack", "SitepackLoader"]
