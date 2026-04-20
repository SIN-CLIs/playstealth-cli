"""Panel plugin loader -- issue #75.

Plugins live in ``panels/<name>/plugin.py`` and expose a ``Panel`` class
with ``name``, ``matches_url(url) -> bool`` and a set of action handlers.
A registry is built from ``platforms/registry.json`` at import time.
"""

from __future__ import annotations

import importlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Panel(Protocol):
    name: str

    def matches_url(self, url: str) -> bool: ...

    def handlers(self) -> dict[str, Callable[..., Any]]: ...


@dataclass
class PanelRegistry:
    panels: list[Panel] = field(default_factory=list)

    def resolve(self, url: str) -> Panel | None:
        for p in self.panels:
            if p.matches_url(url):
                return p
        return None

    def names(self) -> list[str]:
        return [p.name for p in self.panels]


def load_from_registry(path: str | pathlib.Path = "platforms/registry.json") -> PanelRegistry:
    p = pathlib.Path(path)
    if not p.exists():
        return PanelRegistry(panels=[])
    data = json.loads(p.read_text())
    entries = data if isinstance(data, list) else data.get("panels", [])
    panels: list[Panel] = []
    for entry in entries:
        module = entry.get("module")
        cls = entry.get("class", "Panel")
        if not module:
            continue
        try:
            mod = importlib.import_module(module)
            panel_cls = getattr(mod, cls)
            panels.append(panel_cls())
        except Exception as exc:  # noqa: BLE001
            # Do not crash the worker boot for a broken plugin.
            print(f"[panels] skip {module}:{cls} -> {exc}")
    return PanelRegistry(panels=panels)
