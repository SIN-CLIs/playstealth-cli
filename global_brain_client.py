"""
OpenSIN Global Brain HTTP-Client.

WHY: Der HeyPiggy Worker soll Fakten (gegebene Antworten, entdeckte Brand-
Preferenzen, Survey-Strategien die funktioniert haben) mit anderen Agenten
der OpenSIN-Flotte teilen. Das Global Brain (https://github.com/Delqhi/global-brain)
bietet dafuer einen langlaufenden Daemon unter http://127.0.0.1:7070 (bzw.
http://92.5.60.87:7070 in Production).

Dieser Client ist NON-FATAL: wenn der Daemon nicht erreichbar ist, wird
auf Local-File-Fallback (.pcpm/) zurueckgegriffen — der Worker laeuft weiter.

API-Kompatibilitaet: brain-core/daemon.js v4 (ONE-BRAIN):
  POST /attach          -> {projectId, agentId} -> primeContext
  POST /ask             -> {query} -> answer
  POST /ingest          -> {type, text, scope, ...} -> {ok, id, dedup?}
  POST /endSession      -> {consultedRuleIds, success}
  GET  /stats/rich      -> health check
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------


DEFAULT_BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:7070")
DEFAULT_PROJECT_ID = os.environ.get("BRAIN_PROJECT_ID", "heypiggy-survey-worker")
DEFAULT_AGENT_ID = os.environ.get("BRAIN_AGENT_ID", "a2a-sin-worker-heypiggy")
DEFAULT_LOCAL_FALLBACK = Path(os.environ.get("BRAIN_LOCAL_FALLBACK", ".pcpm"))


@dataclass
class PrimeContext:
    """
    Initialer Kontext der beim Attach vom Brain geliefert wird.

    WHY: Das Brain kennt schon nach 10ms alle authored Ultra-Rules,
    project-scoped rules, letzte decisions und die forbidden-Liste. Der
    Worker kann diese in den Vision-Prompt einspielen um sich ab Step 1
    wie ein erfahrener Agent zu verhalten.
    """
    ultra_rules: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    forbidden: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GlobalBrainClient:
    """
    Non-blocking HTTP-Client gegen den brain-core Daemon.

    USAGE:
        brain = GlobalBrainClient()
        ctx = await brain.attach()
        await brain.ingest_fact("Jeremy bevorzugt BMW gegenueber Audi", scope="global")
        await brain.end_session(success=True)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BRAIN_URL,
        project_id: str = DEFAULT_PROJECT_ID,
        agent_id: str = DEFAULT_AGENT_ID,
        persona_username: str | None = None,
        timeout_sec: float = 3.0,
        local_fallback_dir: Path | None = None,
        audit: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.agent_id = agent_id
        self.persona_username = persona_username
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec) if aiohttp else None
        self.local_fallback_dir = local_fallback_dir or DEFAULT_LOCAL_FALLBACK
        self._audit = audit or (lambda *a, **k: None)
        self._is_available: bool | None = None
        self._consulted_rules: list[str] = []

    # ---------------------------------------------------------------
    # Health-Check
    # ---------------------------------------------------------------

    async def is_available(self) -> bool:
        """Pingt den Daemon — cached das Ergebnis fuer die Session."""
        if self._is_available is not None:
            return self._is_available
        if aiohttp is None:
            self._is_available = False
            return False
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(f"{self.base_url}/stats/rich") as resp:
                    self._is_available = resp.status == 200
        except Exception as e:
            self._audit("brain_unavailable", error=str(e))
            self._is_available = False
        return self._is_available

    # ---------------------------------------------------------------
    # Attach — PrimeContext abholen
    # ---------------------------------------------------------------

    async def attach(self) -> PrimeContext:
        """
        Verbindet sich mit dem Brain und holt den initialen Kontext.

        Fallback: liest aus .pcpm/active-context.json falls Daemon down ist.
        """
        if not await self.is_available():
            return self._attach_local_fallback()

        payload = {
            "projectId": self.project_id,
            "agentId": self.agent_id,
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/attach", json=payload
                ) as resp:
                    if resp.status != 200:
                        self._audit("brain_attach_failed", status=resp.status)
                        return self._attach_local_fallback()
                    data = await resp.json()
        except Exception as e:
            self._audit("brain_attach_error", error=str(e))
            return self._attach_local_fallback()

        prime = data.get("primeContext", {})
        ctx = PrimeContext(
            ultra_rules=prime.get("ultraRules", []) or [],
            rules=prime.get("rules", []) or [],
            decisions=prime.get("decisions", []) or [],
            forbidden=prime.get("forbidden", []) or [],
            contradictions=prime.get("contradictions", []) or [],
        )
        self._audit(
            "brain_attached",
            ultra_rules=len(ctx.ultra_rules),
            rules=len(ctx.rules),
            decisions=len(ctx.decisions),
            forbidden=len(ctx.forbidden),
        )
        return ctx

    def _attach_local_fallback(self) -> PrimeContext:
        """Liest den letzten synchronisierten Kontext aus .pcpm/."""
        path = self.local_fallback_dir / "active-context.json"
        if not path.exists():
            return PrimeContext()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return PrimeContext()
        return PrimeContext(
            ultra_rules=data.get("ultraRules", []) or [],
            rules=data.get("rules", []) or [],
            decisions=data.get("decisions", []) or [],
            forbidden=data.get("forbidden", []) or [],
        )

    # ---------------------------------------------------------------
    # Ingest — neue Fakten / Decisions speichern
    # ---------------------------------------------------------------

    async def ingest(
        self,
        entry_type: str,
        text: str,
        scope: str = "project",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Speichert einen neuen Eintrag im Brain.

        entry_type: "fact" | "decision" | "mistake" | "solution" | "rule" | "forbidden"
        scope: "project" | "global"
        """
        # Namespace mit Persona wenn gesetzt — so teilen sich alle Surveys
        # fuer Jeremy dasselbe Fakten-Set, aber Jeremy und Maria bleiben getrennt.
        topic = "/".join(
            filter(None, [self.persona_username, self.project_id])
        ) or self.project_id

        payload: dict[str, Any] = {
            "type": entry_type,
            "text": text,
            "scope": scope,
            "topic": topic,
            "projectId": self.project_id,
            "agentId": self.agent_id,
        }
        if extra:
            payload.update(extra)

        if not await self.is_available():
            return self._ingest_local_fallback(payload)

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/ingest", json=payload
                ) as resp:
                    if resp.status not in (200, 201):
                        self._audit("brain_ingest_failed", status=resp.status)
                        return self._ingest_local_fallback(payload)
                    data = await resp.json()
        except Exception as e:
            self._audit("brain_ingest_error", error=str(e))
            return self._ingest_local_fallback(payload)

        self._audit(
            "brain_ingested",
            type=entry_type,
            scope=scope,
            dedup=data.get("dedup", False),
            id=data.get("id"),
        )
        return data

    def _ingest_local_fallback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Speichert in .pcpm/brain-queue.jsonl fuer spaeteren Sync."""
        self.local_fallback_dir.mkdir(parents=True, exist_ok=True)
        queue = self.local_fallback_dir / "brain-queue.jsonl"
        with queue.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return {"ok": True, "fallback": True, "queued": True}

    # ---------------------------------------------------------------
    # Convenience-Wrapper
    # ---------------------------------------------------------------

    async def ingest_fact(self, text: str, scope: str = "project") -> dict[str, Any]:
        """Kurzform fuer type=fact."""
        return await self.ingest("fact", text, scope=scope)

    async def ingest_decision(
        self, text: str, scope: str = "project"
    ) -> dict[str, Any]:
        """Kurzform fuer type=decision."""
        return await self.ingest("decision", text, scope=scope)

    async def ingest_survey_answer(
        self,
        question: str,
        answer: str,
        topic: str | None = None,
        survey_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Spezialisierter Ingest fuer Survey-Antworten.

        WHY: Survey-Antworten sind Facts mit strukturierten Metadaten. Wir
        wollen sie auffindbar machen ueber (persona, question) damit das
        Consistency-Log auch ueber Runs hinweg funktioniert.
        """
        text = f"Q: {question[:200]} | A: {answer[:200]}"
        extra = {
            "surveyId": survey_id or "",
            "questionTopic": topic or "",
            "persona": self.persona_username or "",
        }
        return await self.ingest("fact", text, scope="project", extra=extra)

    async def ask(self, query: str) -> str | None:
        """
        Fragt das Brain nach einem Kontext-Snippet fuer query.

        Returns den Answer-String oder None wenn nichts gefunden / Daemon down.
        """
        if not await self.is_available():
            return None
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/ask",
                    json={"query": query, "projectId": self.project_id},
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("answer")
        except Exception as e:
            self._audit("brain_ask_error", error=str(e))
            return None

    def track_consulted_rule(self, rule_id: str) -> None:
        """Speichert dass eine Brain-Regel im aktuellen Run konsultiert wurde."""
        if rule_id and rule_id not in self._consulted_rules:
            self._consulted_rules.append(rule_id)

    async def end_session(self, success: bool, extra: dict[str, Any] | None = None) -> None:
        """Signalisiert dem Brain dass die Run-Session beendet ist."""
        if not await self.is_available():
            return
        payload: dict[str, Any] = {
            "projectId": self.project_id,
            "agentId": self.agent_id,
            "success": success,
            "consultedRuleIds": self._consulted_rules,
        }
        if extra:
            payload.update(extra)
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/endSession", json=payload
                ) as resp:
                    if resp.status != 200:
                        self._audit("brain_endsession_failed", status=resp.status)
                    else:
                        self._audit("brain_session_ended", success=success)
        except Exception as e:
            self._audit("brain_endsession_error", error=str(e))


# ---------------------------------------------------------------------------
# Prompt-Block-Generator
# ---------------------------------------------------------------------------


def build_brain_prompt_block(ctx: PrimeContext, max_items_each: int = 5) -> str:
    """
    Verdichtet den PrimeContext in einen kompakten Prompt-Block.

    WHY: Wir wollen dem Vision-LLM die wichtigsten Brain-Insights mitgeben ohne den
    Token-Budget zu sprengen. Harte Cap bei max_items_each pro Kategorie,
    sortiert nach Score (falls vorhanden) damit die wichtigsten oben stehen.
    """
    if not ctx.ultra_rules and not ctx.rules and not ctx.decisions and not ctx.forbidden:
        return ""

    def _sort_and_slice(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda e: float(e.get("score", 0.5)),
            reverse=True,
        )[:max_items_each]

    lines: list[str] = ["===== OPENSIN GLOBAL BRAIN CONTEXT ====="]

    if ctx.ultra_rules:
        lines.append("[ULTRA-RULES — immer befolgen]")
        for r in _sort_and_slice(ctx.ultra_rules):
            text = (r.get("text") or "").strip()[:200]
            if text:
                lines.append(f"- {text}")

    if ctx.forbidden:
        lines.append("[FORBIDDEN — niemals tun]")
        for r in _sort_and_slice(ctx.forbidden):
            text = (r.get("text") or "").strip()[:200]
            if text:
                lines.append(f"- {text}")

    if ctx.rules:
        lines.append("[PROJECT-RULES]")
        for r in _sort_and_slice(ctx.rules):
            text = (r.get("text") or "").strip()[:180]
            if text:
                lines.append(f"- {text}")

    if ctx.decisions:
        lines.append("[LETZTE DECISIONS]")
        for r in _sort_and_slice(ctx.decisions):
            text = (r.get("text") or "").strip()[:180]
            if text:
                lines.append(f"- {text}")

    if ctx.contradictions:
        lines.append("[WARNUNG — widerspruechliche Rules im Brain:]")
        for r in ctx.contradictions[:max_items_each]:
            text = (r.get("text") or "").strip()[:180]
            if text:
                lines.append(f"- {text}")

    return "\n".join(lines)


__all__ = [
    "GlobalBrainClient",
    "PrimeContext",
    "build_brain_prompt_block",
]
