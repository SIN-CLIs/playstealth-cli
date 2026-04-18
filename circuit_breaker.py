#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Circuit Breaker — Schutz gegen NVIDIA NIM API-Überlastung
================================================================================
WHY: NVIDIA NIM gibt bei Überlastung 429 (Rate Limit) oder 500 (Server Error).
     Ohne Circuit Breaker hämmert der Worker weiter gegen die tote API und
     verschwendet Zeit + verschlimmert das Rate Limit.
CONSEQUENCES: Nach N Fehlern in Folge öffnet der Circuit Breaker und blockiert
     weitere Calls für eine konfigurierbare Cooldown-Periode. Danach: Half-Open
     (ein Probe-Call), bei Erfolg: geschlossen, bei Fehler: wieder offen.
================================================================================
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(Enum):
    """
    Die drei Zustände des Circuit Breakers.
    WHY: Standard-Pattern aus der Reliability-Engineering-Literatur.
    CONSEQUENCES: CLOSED = normal, OPEN = blockiert, HALF_OPEN = Probe-Phase.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Circuit Breaker für externe API-Calls (primär NVIDIA NIM).
    WHY: Verhindert dass der Worker bei API-Ausfall in einer Retry-Schleife
         hängt. Spart Zeit und verhindert Rate-Limit-Verschlimmerung.
    CONSEQUENCES: Caller muss vor jedem Call `allow_request()` prüfen und
         nach jedem Call `record_success()` oder `record_failure()` melden.

    Typischer Ablauf:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        if cb.allow_request():
            try:
                result = await nvidia_call(...)
                cb.record_success()
            except Exception:
                cb.record_failure()
        else:
            # Fallback nutzen (z.B. opencode CLI statt NVIDIA)
    """

    # Nach so vielen Fehlern in Folge: Circuit öffnen
    failure_threshold: int = 3
    # So lange warten (Sekunden) bevor ein Probe-Call erlaubt wird
    recovery_timeout: float = 60.0
    # Aktueller Zustand
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    # Zähler für aufeinanderfolgende Fehler
    consecutive_failures: int = field(default=0, init=False)
    # Zeitpunkt der letzten Zustandsänderung
    last_failure_time: float = field(default=0.0, init=False)
    # Gesamtzähler für Monitoring
    total_failures: int = field(default=0, init=False)
    total_successes: int = field(default=0, init=False)
    total_rejected: int = field(default=0, init=False)

    def allow_request(self) -> bool:
        """
        Prüft ob ein Request erlaubt ist.
        WHY: Caller muss VOR jedem externen Call prüfen ob der Circuit offen ist.
        CONSEQUENCES: True = Call erlaubt, False = Call blockiert (Fallback nutzen).
        """
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Prüfe ob Recovery-Timeout abgelaufen → Half-Open
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            self.total_rejected += 1
            return False

        # HALF_OPEN: Ein einzelner Probe-Call ist erlaubt
        return True

    def record_success(self):
        """
        Meldet einen erfolgreichen API-Call.
        WHY: Bei Erfolg im HALF_OPEN-State → Circuit schließen (API ist wieder da).
        CONSEQUENCES: Fehler-Zähler wird zurückgesetzt, Circuit geht auf CLOSED.
        """
        self.total_successes += 1
        self.consecutive_failures = 0
        if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            self.state = CircuitState.CLOSED

    def record_failure(self):
        """
        Meldet einen fehlgeschlagenen API-Call.
        WHY: Nach N Fehlern in Folge → Circuit öffnen um weitere Calls zu blockieren.
        CONSEQUENCES: Bei HALF_OPEN → sofort wieder OPEN (Probe gescheitert).
                      Bei CLOSED → Zähler erhöhen, ggf. öffnen.
        """
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Probe-Call gescheitert → sofort wieder OPEN
            self.state = CircuitState.OPEN
        elif self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def reset(self):
        """
        Setzt den Circuit Breaker komplett zurück.
        WHY: Für Tests und manuelle Recovery nützlich.
        CONSEQUENCES: Alle Zähler auf 0, State auf CLOSED.
        """
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self.total_failures = 0
        self.total_successes = 0
        self.total_rejected = 0

    @property
    def is_open(self) -> bool:
        """True wenn der Circuit offen ist (Calls werden blockiert)."""
        return self.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        """True wenn der Circuit geschlossen ist (normaler Betrieb)."""
        return self.state == CircuitState.CLOSED

    def status_dict(self) -> dict[str, object]:
        """
        Gibt den aktuellen Status als Dict zurück (für Logging/Monitoring).
        WHY: Strukturierter Status für Audit-Log und Observability.
        CONSEQUENCES: Alle relevanten Metriken auf einen Blick.
        """
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "total_rejected": self.total_rejected,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "time_since_last_failure": (
                round(time.time() - self.last_failure_time, 1) if self.last_failure_time > 0 else 0
            ),
        }
