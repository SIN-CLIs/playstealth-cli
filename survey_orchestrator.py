#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Survey Orchestrator — Multi-Survey Queue mit Auto-Detect + Explizite Liste
================================================================================
WHY: Der Worker soll in einem Lauf BELIEBIG VIELE Umfragen hintereinander
     abarbeiten (5, 10, 20 — egal). Bisher endete ein Lauf nach der ersten
     `survey_done`-Seite im Idle-Zustand.
CONSEQUENCES: Der Orchestrator übernimmt nach jedem `survey_done`:
     1) Feiert den Abschluss (Audit + Session-Backup)
     2) Versucht einen Redirect/Link zur nächsten Umfrage zu erkennen
     3) Fällt zurück auf eine explizite URL-Liste (HEYPIGGY_SURVEY_URLS env)
     4) Fällt weiter zurück auf "Dashboard öffnen + höchsten €-Betrag klicken"
     5) Beendet den Lauf erst wenn:
        - Alle geplanten Surveys fertig sind
        - Keine neuen Surveys im Dashboard verfügbar sind
        - Ein Hard-Limit (MAX_SURVEYS_PER_RUN) erreicht ist
        - Der Cooldown zwischen Surveys nicht eingehalten werden kann

STATE:
  IDLE → RUNNING → COMPLETED_ONE → NAVIGATING_NEXT → RUNNING …
                                 → DONE (wenn Queue leer und kein Auto-Detect)

KONFIGURATION:
  - HEYPIGGY_SURVEY_URLS   Komma-Liste expliziter Start-URLs
  - HEYPIGGY_MAX_SURVEYS   Obergrenze pro Run (default: 25)
  - HEYPIGGY_COOLDOWN_SEC  Mindest-Pause zwischen Surveys (default: 4.0)
  - HEYPIGGY_AUTODETECT    "1" → Auto-Detect aktiv, "0" → nur explizite Liste
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Awaitable, Callable


# ============================================================================
# STATES & DATACLASSES
# ============================================================================


class QueueState(Enum):
    """Alle möglichen Zustände der Survey-Queue."""

    IDLE = auto()
    RUNNING = auto()
    COMPLETED_ONE = auto()
    NAVIGATING_NEXT = auto()
    COOLDOWN = auto()
    NO_MORE_AVAILABLE = auto()
    LIMIT_REACHED = auto()
    ABORTED = auto()
    DONE = auto()


@dataclass
class SurveyRecord:
    """Eine einzelne Survey-Durchführung."""

    index: int
    start_url: str
    start_time: float
    end_time: float = 0.0
    success: bool = False
    steps_used: int = 0
    end_reason: str = ""
    questions_answered: int = 0
    estimated_reward: str = ""

    @property
    def duration_sec(self) -> float:
        if self.end_time <= 0:
            return 0.0
        return round(self.end_time - self.start_time, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_url": self.start_url,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_sec": self.duration_sec,
            "success": self.success,
            "steps_used": self.steps_used,
            "end_reason": self.end_reason,
            "questions_answered": self.questions_answered,
            "estimated_reward": self.estimated_reward,
        }


# ============================================================================
# ORCHESTRATOR
# ============================================================================


BridgeCallable = Callable[[str, dict[str, Any]], Awaitable[Any]]


class SurveyOrchestrator:
    """
    Koordiniert mehrere Surveys in einem einzigen Worker-Run.

    USAGE:
        orch = SurveyOrchestrator(
            execute_bridge=execute_bridge,
            tab_params_factory=_tab_params,
            explicit_urls=["https://heypiggy.com/...", ...],
            dashboard_url="https://www.heypiggy.com/",
            audit=audit_fn,
            autodetect=True,
            max_surveys=10,
            cooldown_sec=4.0,
        )

        await orch.begin()                                   # startet die 1. Survey
        …
        next_state = await orch.on_survey_completed(record)  # am Ende jeder Survey
        if next_state == QueueState.DONE: break
    """

    NEXT_SURVEY_SELECTORS_JS = r"""
    (function() {
      // Klassische "Nächste Umfrage verfügbar" / "Start next survey" Buttons
      var texts = ['nächste umfrage', 'next survey', 'weitere umfrage',
                   'umfrage starten', 'start survey', 'start next',
                   'continue', 'weiter zur nächsten', 'mehr umfragen'];
      var candidates = Array.from(document.querySelectorAll(
        'a, button, .survey-item, [role="button"], [class*="card"]'
      ));
      for (var i = 0; i < candidates.length; i++) {
        var el = candidates[i];
        var txt = (el.textContent || '').toLowerCase().trim();
        var r = el.getBoundingClientRect();
        if (r.width < 10 || r.height < 10) continue;
        for (var j = 0; j < texts.length; j++) {
          if (txt.indexOf(texts[j]) !== -1) {
            var sel = el.id ? ('#' + el.id) : (el.tagName + '.' +
                     ((el.className || '').split(' ').filter(Boolean)[0] || ''));
            return {
              found: true,
              selector: sel,
              text: txt.substring(0, 80),
              href: el.href || '',
              tag: el.tagName
            };
          }
        }
      }
      return { found: false };
    })();
    """

    HIGHEST_REWARD_JS = r"""
    (function() {
      // Findet die lukrativste verfügbare Survey auf dem Dashboard
      var items = Array.from(document.querySelectorAll('.survey-item, [id^="survey-"]'));
      if (!items.length) return { found: false };
      var best = null;
      var bestVal = -1;
      for (var i = 0; i < items.length; i++) {
        var el = items[i];
        var txt = el.textContent || '';
        var m = txt.match(/(\d+[\.,]?\d*)\s*€/);
        if (!m) continue;
        var val = parseFloat(m[1].replace(',', '.'));
        if (isNaN(val)) continue;
        if (val > bestVal) {
          bestVal = val;
          best = el;
        }
      }
      if (!best) return { found: false };
      return {
        found: true,
        selector: best.id ? ('#' + best.id) : 'div.survey-item',
        reward: bestVal + '€',
        text: (best.textContent || '').substring(0, 120).trim()
      };
    })();
    """

    def __init__(
        self,
        *,
        execute_bridge: BridgeCallable,
        tab_params_factory: Callable[[], dict[str, Any]],
        dashboard_url: str = "https://www.heypiggy.com/",
        explicit_urls: list[str] | None = None,
        autodetect: bool = True,
        max_surveys: int = 25,
        cooldown_sec: float = 4.0,
        cooldown_jitter: float = 2.0,
        audit: Callable[..., None] | None = None,
        history_path: Path | None = None,
        should_skip: Callable[[str], Awaitable[tuple[bool, str]]] | None = None,
        max_skip_attempts: int = 8,
    ) -> None:
        self._bridge = execute_bridge
        self._tab_params = tab_params_factory
        self._dashboard_url = dashboard_url
        self._explicit_queue: deque[str] = deque(explicit_urls or [])
        self._autodetect = autodetect
        self._max_surveys = max(1, max_surveys)
        self._cooldown_sec = max(0.0, cooldown_sec)
        self._cooldown_jitter = max(0.0, cooldown_jitter)
        self._audit = audit or (lambda *a, **kw: None)
        self._history_path = history_path or Path("/tmp/heypiggy_queue_history.json")
        # Skip-Filter: gibt (skip, reason) zurueck. True = URL ueberspringen.
        # WHY: Das Brain kennt bekannte DQ-Screener aus frueheren Runs. Statt
        # die Umfrage erneut durchzulaufen und wieder disqualifiziert zu werden,
        # lassen wir die Orchestrierung sie ganz ueberspringen.
        self._should_skip = should_skip
        self._max_skip_attempts = max(1, max_skip_attempts)
        # Tracking: welche URLs haben wir im aktuellen Run schon versucht.
        # Verhindert Endlosschleife wenn das Dashboard immer wieder dieselbe
        # Top-Kachel vorschlaegt.
        self._attempted_urls: set[str] = set()
        self._skipped_count: int = 0

        self._state: QueueState = QueueState.IDLE
        self._records: list[SurveyRecord] = []
        self._current: SurveyRecord | None = None
        self._session_start = time.monotonic()

    # ------------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------------

    @property
    def state(self) -> QueueState:
        return self._state

    @property
    def records(self) -> list[SurveyRecord]:
        return list(self._records)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self._records if r.success)

    @property
    def attempted_count(self) -> int:
        return len(self._records)

    def stats_summary(self) -> dict[str, Any]:
        """Aggregiert Statistiken für den Final-Report des Workers."""
        total_duration = sum(r.duration_sec for r in self._records)
        return {
            "attempted": self.attempted_count,
            "completed": self.completed_count,
            "failed": self.attempted_count - self.completed_count,
            "total_duration_sec": round(total_duration, 1),
            "avg_duration_sec": (
                round(total_duration / self.attempted_count, 1)
                if self.attempted_count
                else 0.0
            ),
            "state": self._state.name,
            "records": [r.to_dict() for r in self._records],
        }

    async def begin(self) -> SurveyRecord | None:
        """
        Startet die erste Survey: explizite URL oder Dashboard + höchster Reward.
        WHY: Der Worker soll nicht wissen müssen "starte ich mit URL X oder mit
             dem Dashboard" — das ist Orchestrator-Logik.
        CONSEQUENCES: Gibt SurveyRecord zurück, der ab sofort als _current gilt.
             Wenn nichts gestartet werden kann → None + state=NO_MORE_AVAILABLE.
        """
        self._state = QueueState.NAVIGATING_NEXT
        next_url = await self._resolve_next_url_with_skip()
        if not next_url:
            self._state = QueueState.NO_MORE_AVAILABLE
            self._audit("queue_begin_no_survey_found")
            return None

        await self._navigate_to(next_url)
        record = SurveyRecord(
            index=len(self._records) + 1,
            start_url=next_url,
            start_time=time.monotonic(),
        )
        self._records.append(record)
        self._current = record
        self._state = QueueState.RUNNING
        self._audit("queue_begin", index=record.index, url=next_url)
        return record

    async def on_survey_completed(
        self,
        *,
        success: bool,
        steps_used: int,
        end_reason: str = "",
        questions_answered: int = 0,
    ) -> QueueState:
        """
        Wird aufgerufen wenn der Worker eine Umfrage fertig hat (survey_done).
        Entscheidet über nächste Schritte und liefert den resultierenden Zustand.
        WHY: Der Worker-Main-Loop bleibt dumm — er ruft nur diese Methode auf
             und bekommt "DONE" oder "RUNNING" zurück.
        CONSEQUENCES: Aktualisiert Record, schreibt History, startet ggf. Cooldown
             und navigiert zur nächsten Survey.
        """
        if self._current is None:
            # Defensiv: Orchestrator kannte diese Survey nicht — lege eine nach
            self._current = SurveyRecord(
                index=len(self._records) + 1,
                start_url="(unknown)",
                start_time=time.monotonic() - 1,
            )
            self._records.append(self._current)

        self._current.end_time = time.monotonic()
        self._current.success = success
        self._current.steps_used = steps_used
        self._current.end_reason = end_reason
        self._current.questions_answered = questions_answered
        self._persist_history()
        self._audit(
            "queue_survey_completed",
            index=self._current.index,
            success=success,
            duration_sec=self._current.duration_sec,
            steps=steps_used,
            reason=end_reason,
        )
        self._state = QueueState.COMPLETED_ONE

        # Hard-Limit?
        if self.attempted_count >= self._max_surveys:
            self._state = QueueState.LIMIT_REACHED
            self._audit("queue_limit_reached", max_surveys=self._max_surveys)
            return self._state

        # Cooldown (menschliche Pause zwischen Surveys)
        if self._cooldown_sec > 0:
            self._state = QueueState.COOLDOWN
            wait = self._cooldown_sec + random.uniform(0, self._cooldown_jitter)
            self._audit("queue_cooldown_start", wait_sec=round(wait, 1))
            await asyncio.sleep(wait)

        # Nächste Survey suchen (mit Skip-Filter falls konfiguriert)
        self._state = QueueState.NAVIGATING_NEXT
        next_url = await self._resolve_next_url_with_skip()
        if not next_url:
            self._state = QueueState.NO_MORE_AVAILABLE
            self._current = None
            self._audit("queue_empty", attempted=self.attempted_count)
            return self._state

        # Tatsächlich zur nächsten navigieren
        await self._navigate_to(next_url)
        record = SurveyRecord(
            index=len(self._records) + 1,
            start_url=next_url,
            start_time=time.monotonic(),
        )
        self._records.append(record)
        self._current = record
        self._state = QueueState.RUNNING
        self._audit("queue_next_started", index=record.index, url=next_url)
        return self._state

    def abort(self, reason: str) -> None:
        """Manueller Abbruch (z.B. bei kritischem Fehler)."""
        self._audit("queue_aborted", reason=reason)
        if self._current and self._current.end_time == 0.0:
            self._current.end_time = time.monotonic()
            self._current.success = False
            self._current.end_reason = f"aborted: {reason}"
        self._state = QueueState.ABORTED
        self._persist_history()

    def finalize(self) -> dict[str, Any]:
        """Schließt den Run ab und liefert die finale Zusammenfassung."""
        if self._state not in (QueueState.ABORTED, QueueState.LIMIT_REACHED):
            self._state = QueueState.DONE
        self._persist_history()
        return self.stats_summary()

    # ------------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------------

    async def _resolve_next_url_with_skip(self) -> str | None:
        """
        Wrapper um _resolve_next_url() der den Skip-Filter anwendet:
        Wenn should_skip(url) True liefert, wird die URL verworfen und die
        naechste Kandidatin angefragt. Nach max_skip_attempts fehlgeschlagenen
        Versuchen gibt es auf, damit die Schleife nicht unendlich laeuft.

        WHY: Das Brain hat aus frueheren Runs gelernt welche Umfragen (URL-
        Pattern oder Screener-Frage) uns disqualifizieren. Die wollen wir
        gar nicht erst oeffnen — kostet Zeit und erhoeht den Trust-Abbau
        beim Panel-Provider.
        """
        for attempt in range(self._max_skip_attempts):
            url = await self._resolve_next_url()
            if not url:
                return None

            # Dedupe: dieselbe URL in einem Run nicht zweimal probieren
            norm = url.split("#", 1)[0]
            if norm in self._attempted_urls:
                self._audit("queue_skip_duplicate", url=url, attempt=attempt)
                continue

            # Brain-basierter Skip-Check
            if self._should_skip is not None:
                try:
                    skip, reason = await self._should_skip(url)
                except Exception as e:
                    self._audit("queue_skip_check_error", error=str(e))
                    skip, reason = False, ""
                if skip:
                    self._skipped_count += 1
                    self._attempted_urls.add(norm)
                    self._audit(
                        "queue_skip_brain",
                        url=url,
                        reason=(reason or "unknown")[:120],
                        attempt=attempt,
                    )
                    continue

            self._attempted_urls.add(norm)
            return url

        self._audit(
            "queue_skip_exhausted",
            attempts=self._max_skip_attempts,
            skipped_total=self._skipped_count,
        )
        return None

    async def _resolve_next_url(self) -> str | None:
        """
        Ermittelt die nächste Survey-URL in dieser Priorität:
          1) Explizite Queue (HEYPIGGY_SURVEY_URLS / Konstruktor)
          2) Auto-Detect: Next-Survey-Button auf aktueller Seite
          3) Dashboard-Fallback: höchster Reward
        """
        # 1) Explizite Queue
        if self._explicit_queue:
            url = self._explicit_queue.popleft()
            self._audit("queue_url_explicit", url=url)
            return url

        if not self._autodetect:
            return None

        # 2) Auto-Detect: gibt es auf der aktuellen Seite einen "Nächste"-Button?
        next_button = await self._detect_next_button()
        if next_button:
            url = next_button.get("href", "")
            if url:
                self._audit("queue_url_autodetect_href", url=url)
                return url
            # Button hat keinen href (SPA-Button) → direkt klicken
            selector = next_button.get("selector", "")
            if selector:
                try:
                    await self._bridge(
                        "ghost_click",
                        {"selector": selector, **self._tab_params()},
                    )
                    await asyncio.sleep(2.0)
                    current = await self._current_url()
                    if current:
                        self._audit("queue_url_autodetect_spa", url=current)
                        return current
                except Exception as e:
                    self._audit("queue_autodetect_click_error", error=str(e))

        # 3) Dashboard-Fallback
        await self._navigate_to(self._dashboard_url)
        await asyncio.sleep(2.5)
        best = await self._find_best_dashboard_survey()
        if best and best.get("selector"):
            # Klicken + URL der neuen Seite lesen
            try:
                await self._bridge(
                    "ghost_click",
                    {"selector": best["selector"], **self._tab_params()},
                )
                await asyncio.sleep(2.5)
                current = await self._current_url()
                if current and current != self._dashboard_url:
                    if self._current is not None:
                        self._current.estimated_reward = best.get("reward", "")
                    self._audit(
                        "queue_url_dashboard_best",
                        reward=best.get("reward", ""),
                        url=current,
                    )
                    return current
            except Exception as e:
                self._audit("queue_dashboard_click_error", error=str(e))

        return None

    async def _detect_next_button(self) -> dict[str, Any] | None:
        try:
            result = await self._bridge(
                "execute_javascript",
                {"script": self.NEXT_SURVEY_SELECTORS_JS, **self._tab_params()},
            )
            data = result.get("result") if isinstance(result, dict) else None
            if isinstance(data, dict) and data.get("found"):
                return data
        except Exception as e:
            self._audit("queue_detect_error", error=str(e))
        return None

    async def _find_best_dashboard_survey(self) -> dict[str, Any] | None:
        try:
            result = await self._bridge(
                "execute_javascript",
                {"script": self.HIGHEST_REWARD_JS, **self._tab_params()},
            )
            data = result.get("result") if isinstance(result, dict) else None
            if isinstance(data, dict) and data.get("found"):
                return data
        except Exception as e:
            self._audit("queue_dashboard_scan_error", error=str(e))
        return None

    async def _current_url(self) -> str:
        try:
            info = await self._bridge("get_page_info", self._tab_params())
            if isinstance(info, dict):
                return str(info.get("url", "") or "")
        except Exception:
            pass
        return ""

    async def _navigate_to(self, url: str) -> None:
        try:
            await self._bridge("navigate", {"url": url, **self._tab_params()})
        except Exception as e:
            self._audit("queue_navigate_error", url=url, error=str(e))

    def _persist_history(self) -> None:
        try:
            payload = {
                "session_start": datetime.fromtimestamp(
                    time.time() - (time.monotonic() - self._session_start),
                    tz=timezone.utc,
                ).isoformat(),
                "state": self._state.name,
                "stats": self.stats_summary(),
            }
            self._history_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            self._audit("queue_history_persist_error", error=str(e))


# ============================================================================
# FACTORY aus ENV
# ============================================================================


def build_orchestrator_from_env(
    *,
    execute_bridge: BridgeCallable,
    tab_params_factory: Callable[[], dict[str, Any]],
    dashboard_url: str = "https://www.heypiggy.com/",
    audit: Callable[..., None] | None = None,
) -> SurveyOrchestrator:
    """
    Baut einen SurveyOrchestrator aus Environment-Variablen.
    WHY: Der Worker soll nicht wissen müssen wie die Queue konfiguriert wird.
         Ein einziger Factory-Call reicht.
    """
    explicit = os.environ.get("HEYPIGGY_SURVEY_URLS", "").strip()
    urls: list[str] = []
    if explicit:
        urls = [u.strip() for u in explicit.split(",") if u.strip()]

    max_surveys = int(os.environ.get("HEYPIGGY_MAX_SURVEYS", "25"))
    cooldown = float(os.environ.get("HEYPIGGY_COOLDOWN_SEC", "4.0"))
    cooldown_jitter = float(os.environ.get("HEYPIGGY_COOLDOWN_JITTER", "2.0"))
    autodetect = os.environ.get("HEYPIGGY_AUTODETECT", "1") != "0"

    return SurveyOrchestrator(
        execute_bridge=execute_bridge,
        tab_params_factory=tab_params_factory,
        dashboard_url=os.environ.get("HEYPIGGY_DASHBOARD_URL", dashboard_url),
        explicit_urls=urls,
        autodetect=autodetect,
        max_surveys=max_surveys,
        cooldown_sec=cooldown,
        cooldown_jitter=cooldown_jitter,
        audit=audit,
    )
