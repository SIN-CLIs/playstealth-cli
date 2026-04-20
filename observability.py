#!/usr/bin/env python3
# ================================================================================
# DATEI: observability.py
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
Observability — Strukturiertes Logging + Run-Summary Metriken
================================================================================
WHY: Der Worker loggte bisher nur über print() und ein einfaches JSONL-Audit-Log.
     Keine strukturierten Metriken, keine Timing-Daten, keine Aggregation.
     Debugging erforderte manuelles Greppen durch tausende Log-Zeilen.
CONSEQUENCES: Strukturierte Metriken pro Run (Timing, Erfolgsrate, Fehlerarten).
     Drop-in RunSummary Klasse die der Worker mitführt und am Ende serialisiert.
================================================================================
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StepMetric:
    """
    Metriken für einen einzelnen Worker-Schritt.
    WHY: Jeder Schritt hat unterschiedliche Dauern (Vision-Call, Bridge-Call, Delay).
         Ohne granulare Timing-Daten wissen wir nicht wo die Zeit verloren geht.
    CONSEQUENCES: Pro Schritt: Dauer, Verdict, Page-State, Aktion.
    """

    step_number: int
    timestamp: float
    duration_seconds: float = 0.0
    verdict: str = ""
    page_state: str = ""
    action: str = ""
    success: bool = True
    error: str = ""


@dataclass
class RunSummary:
    # ========================================================================
    # KLASSE: RunSummary
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """
    Aggregierte Metriken für einen kompletten Worker-Run.
    WHY: Am Ende jedes Runs brauchen wir eine Zusammenfassung:
         Wie viele Schritte, wie lange, wie viele Fails, welche Fehlerarten.
    CONSEQUENCES: Serialisierbar als JSON, direkt in run_summary.json schreibbar.
    """

    run_id: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0

    # Schritt-Zähler
    total_steps: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    retry_steps: int = 0

    # Timing
    total_vision_calls: int = 0
    total_vision_time_seconds: float = 0.0
    total_bridge_calls: int = 0
    total_bridge_time_seconds: float = 0.0

    # Fehler-Klassifikation
    captcha_encounters: int = 0
    loop_detections: int = 0
    timeout_errors: int = 0
    bridge_errors: int = 0
    vision_errors: int = 0

    # Ergebnis
    final_page_state: str = ""
    exit_reason: str = ""
    surveys_completed: int = 0

    # EUR-Totalizer
    # WHY: HeyPiggy bestaetigt jede Umfrage mit "+0.XX EUR gutgeschrieben".
    #      Der Worker soll diese Summen aggregieren damit der User sieht
    #      wieviel er pro Run verdient. Deduplizierung ueber seen_rewards
    #      verhindert dass derselbe Banner in mehreren Scans doppelt zaehlt.
    earnings_eur: float = 0.0
    seen_rewards: set[str] = field(default_factory=set)
    surveys_disqualified: int = 0

    # Detail-Metriken pro Schritt (optional, kann groß werden)
    step_metrics: list[StepMetric] = field(default_factory=list)

    def record_step(
        self,
        step_number: int,
        verdict: str,
        page_state: str,
        action: str = "",
        duration: float = 0.0,
        success: bool = True,
        error: str = "",
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: record_step
    # PARAMETER: 
        self,
        step_number: int,
        verdict: str,
        page_state: str,
        action: str = "",
        duration: float = 0.0,
        success: bool = True,
        error: str = "",
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """
        Zeichnet einen Schritt auf.
        WHY: Zentrale Methode statt verstreuter Zähler-Inkremente.
        CONSEQUENCES: Alle Zähler werden konsistent aktualisiert.
        """
        self.total_steps += 1

        if verdict == "PROCEED":
            self.successful_steps += 1
        elif verdict == "RETRY":
            self.retry_steps += 1
        elif verdict == "STOP":
            self.failed_steps += 1

        if not success:
            self.failed_steps += 1

        self.step_metrics.append(
            StepMetric(
                step_number=step_number,
                timestamp=time.time(),
                duration_seconds=duration,
                verdict=verdict,
                page_state=page_state,
                action=action,
                success=success,
                error=error,
            )
        )

    def record_vision_call(self, duration_seconds: float):
    # -------------------------------------------------------------------------
    # FUNKTION: record_vision_call
    # PARAMETER: self, duration_seconds: float
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Zeichnet einen Vision-API-Call auf (Timing)."""
        self.total_vision_calls += 1
        self.total_vision_time_seconds += duration_seconds

    def record_bridge_call(self, duration_seconds: float):
        """Zeichnet einen Bridge-Call auf (Timing)."""
        self.total_bridge_calls += 1
        self.total_bridge_time_seconds += duration_seconds

    def record_survey_completed(self):
        """Zeichnet einen erfolgreichen Survey-Abschluss auf."""
        self.surveys_completed += 1

    def record_survey_disqualified(self):
        """Zeichnet einen DQ (Screener-Fail) auf."""
        self.surveys_disqualified += 1

    def record_earning(self, amount_eur: float, dedup_key: str = "") -> bool:
        """
        Addiert amount_eur zur Session-Summe. Returns True wenn neu gebucht,
        False wenn der dedup_key schon gesehen wurde.
        WHY: Der Dashboard-Scanner sieht denselben Reward-Banner oft mehrfach
             (Polling) -> Deduplication verhindert falsche Doppelbuchung.
        CONSEQUENCES: User sieht den echten Netto-Verdienst pro Run.
        """
        if amount_eur <= 0 or amount_eur > 50:
            return False  # Sanity-Guard: mehr als 50 EUR pro Einzelumfrage ist nie echt
        key = dedup_key or f"{round(amount_eur, 2)}"
        if key in self.seen_rewards:
            return False
        self.seen_rewards.add(key)
        self.earnings_eur = round(self.earnings_eur + amount_eur, 2)
        return True

    def finalize(self, exit_reason: str = "", page_state: str = ""):
    # -------------------------------------------------------------------------
    # FUNKTION: finalize
    # PARAMETER: self, exit_reason: str = "", page_state: str = ""
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """
        Finalisiert den Run (setzt end_time und Exit-Info).
        WHY: Muss am Ende des Runs einmal aufgerufen werden.
        CONSEQUENCES: duration_seconds wird berechenbar.
        """
        self.end_time = time.time()
        self.exit_reason = exit_reason
        self.final_page_state = page_state

    @property
    def duration_seconds(self) -> float:
        """Gesamtdauer des Runs in Sekunden."""
        end = self.end_time if self.end_time > 0 else time.time()
        return round(end - self.start_time, 2)

    @property
    def success_rate(self) -> float:
        """Erfolgsrate als Float (0.0 - 1.0)."""
        if self.total_steps == 0:
            return 0.0
        return round(self.successful_steps / self.total_steps, 4)

    @property
    def avg_vision_time(self) -> float:
        """Durchschnittliche Vision-Call-Dauer in Sekunden."""
        if self.total_vision_calls == 0:
            return 0.0
        return round(self.total_vision_time_seconds / self.total_vision_calls, 3)

    @property
    def avg_bridge_time(self) -> float:
        """Durchschnittliche Bridge-Call-Dauer in Sekunden."""
        if self.total_bridge_calls == 0:
            return 0.0
        return round(self.total_bridge_time_seconds / self.total_bridge_calls, 3)

    def to_dict(self, include_steps: bool = False) -> dict[str, object]:
        """
        Serialisiert den RunSummary als Dict.
        WHY: Für JSON-Export und Audit-Logging.
        CONSEQUENCES: include_steps=False spart Speicher bei vielen Schritten.
        """
        d: dict[str, object] = {
            "run_id": self.run_id,
            "duration_seconds": self.duration_seconds,
            "total_steps": self.total_steps,
            "successful_steps": self.successful_steps,
            "failed_steps": self.failed_steps,
            "retry_steps": self.retry_steps,
            "success_rate": self.success_rate,
            "total_vision_calls": self.total_vision_calls,
            "total_vision_time_seconds": round(self.total_vision_time_seconds, 2),
            "avg_vision_time": self.avg_vision_time,
            "total_bridge_calls": self.total_bridge_calls,
            "total_bridge_time_seconds": round(self.total_bridge_time_seconds, 2),
            "avg_bridge_time": self.avg_bridge_time,
            "captcha_encounters": self.captcha_encounters,
            "loop_detections": self.loop_detections,
            "timeout_errors": self.timeout_errors,
            "bridge_errors": self.bridge_errors,
            "vision_errors": self.vision_errors,
            "final_page_state": self.final_page_state,
            "exit_reason": self.exit_reason,
            "surveys_completed": self.surveys_completed,
            "surveys_disqualified": self.surveys_disqualified,
            "earnings_eur": round(self.earnings_eur, 2),
        }
        if include_steps:
            d["steps"] = [
                {
                    "step": s.step_number,
                    "verdict": s.verdict,
                    "page_state": s.page_state,
                    "action": s.action,
                    "duration": round(s.duration_seconds, 3),
                    "success": s.success,
                    "error": s.error,
                }
                for s in self.step_metrics
            ]
        return d

    def save_to_file(self, path: Path, include_steps: bool = True):
    # -------------------------------------------------------------------------
    # FUNKTION: save_to_file
    # PARAMETER: self, path: Path, include_steps: bool = True
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """
        Speichert den RunSummary als JSON-Datei.
        WHY: Persistente Metriken für Post-Mortem und Trend-Analyse.
        CONSEQUENCES: Überschreibt existierende Datei (letzter Stand zählt).
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(
            json.dumps(self.to_dict(include_steps=include_steps), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def print_summary(self):
    # -------------------------------------------------------------------------
    # FUNKTION: print_summary
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """Gibt eine kompakte Zusammenfassung auf stdout aus."""
        d = self.duration_seconds
        mins = int(d // 60)
        secs = int(d % 60)
        print(f"\n{'=' * 60}")
        print(f"📊 RUN SUMMARY — {self.run_id}")
        print(f"   Dauer: {mins}m {secs}s")
        print(
            f"   Schritte: {self.total_steps} (✅ {self.successful_steps} | 🔄 {self.retry_steps} | ❌ {self.failed_steps})"
        )
        print(f"   Erfolgsrate: {self.success_rate:.0%}")
        print(f"   Vision: {self.total_vision_calls} Calls, ⌀ {self.avg_vision_time:.1f}s")
        print(f"   Bridge: {self.total_bridge_calls} Calls, ⌀ {self.avg_bridge_time:.1f}s")
        print(f"   Surveys abgeschlossen: {self.surveys_completed}")
        if self.surveys_disqualified:
            print(f"   Surveys disqualifiziert: {self.surveys_disqualified}")
        if self.earnings_eur > 0:
            print(f"   VERDIENT: {self.earnings_eur:.2f} EUR")
        if self.captcha_encounters:
            print(f"   ⚠️ Captchas: {self.captcha_encounters}")
        if self.loop_detections:
            print(f"   ⚠️ Loops: {self.loop_detections}")
        print(f"   Exit: {self.exit_reason} | State: {self.final_page_state}")
        print(f"{'=' * 60}\n")
