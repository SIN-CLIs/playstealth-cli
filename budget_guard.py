"""
Token- und Request-Budget-Circuit-Breaker.

WHY (DE): Vision-LLMs werden pro 1M Tokens abgerechnet (NVIDIA NIM, OpenAI,
Anthropic). Eine Umfrage mit 30 Schritten und je 4k Prompt + 500 Output Token
kostet ~135k Token — bei Dauerlauf ueber mehrere Umfragen kumuliert das auf
Millionen Token pro Tag. Ohne Budget-Guard laeuft der Worker still weiter bis
die Gateway-Quota erschoepft ist UND zusaetzlich waehrend jedes API-Retry
Kosten produziert.

KONSEQUENZEN:
- Der Guard haelt Token-Counter und Request-Counter pro Run. Wenn eines der
  Limits erreicht ist, liefert `check()` einen Breaker-Status der den Worker
  zum geordneten Herunterfahren zwingt (aktuelle Umfrage zu Ende bringen,
  danach keine neue starten).
- Limits werden ueber ENV gesetzt (BUDGET_MAX_TOKENS, BUDGET_MAX_REQUESTS,
  BUDGET_MAX_EUR_SPEND) damit User je nach Budget tunen koennen.
- Alle Zuwachs-Calls sind thread-/async-sicher (nur einfache Additions auf
  int/float — Python's GIL reicht fuer unsere Last).
- Der Guard speichert auch pro-Modell Kosten (GPT-4-Preis vs. GPT-4o-mini etc.)
  damit `estimate_spend_eur()` realistische EUR-Schaetzungen zurueckgibt.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable


# Sehr grobe Cost-per-1M-Tokens-Tabelle (EUR). WHY: Wir brauchen keine
# Exaktheit — die Schaetzung dient dem Circuit-Breaker, nicht der Buchhaltung.
# Werte sind konservativ hoch angesetzt damit wir eher frueher als spaeter
# stoppen.
COST_PER_MTOK_EUR: dict[str, tuple[float, float]] = {
    # model_prefix: (input_eur_per_mtok, output_eur_per_mtok)
    "gpt-4o": (4.50, 13.50),
    "gpt-4o-mini": (0.14, 0.55),
    "gpt-5": (10.0, 30.0),
    "claude-3-5-sonnet": (2.70, 13.50),
    "claude-opus": (13.50, 67.50),
    "gemini-1.5-pro": (3.00, 9.00),
    "gemini-1.5-flash": (0.13, 0.53),
    "gemini-3": (1.50, 6.00),
    "llama-3": (0.20, 0.30),
    "llama-4": (0.30, 0.45),
    "nvidia/": (0.20, 0.30),
    "nemotron": (0.20, 0.30),
    "parakeet": (0.05, 0.05),
    "cosmos": (0.40, 0.60),
}


def _cost_for_model(model: str) -> tuple[float, float]:
    """Liefert (input_eur, output_eur) per MTok fuer ein Modell."""
    m = (model or "").lower()
    for prefix, cost in COST_PER_MTOK_EUR.items():
        if prefix in m:
            return cost
    # Default: GPT-4o-Preis (konservativ)
    return COST_PER_MTOK_EUR["gpt-4o"]


@dataclass
class BudgetState:
    """Laufende Zaehler fuer einen Run."""

    started_at: float = field(default_factory=time.time)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    total_eur: float = 0.0
    # Per-Modell Breakdown fuer run_summary
    per_model: dict[str, dict[str, float]] = field(default_factory=dict)
    # Breaker-Flags
    breaker_tripped: bool = False
    breaker_reason: str = ""


class BudgetGuard:
    """
    Verwaltet Limits und Counter fuer einen Worker-Run.

    Usage:
        guard = BudgetGuard.from_env()
        ...
        resp = await llm.chat(...)
        guard.record_usage(
            model="gpt-4o",
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
        )
        if guard.tripped():
            # Orchestrator liest das und beendet nach aktueller Umfrage
            break
    """

    def __init__(
        self,
        *,
        max_tokens: int = 0,
        max_requests: int = 0,
        max_eur: float = 0.0,
        audit: Callable[..., None] | None = None,
    ) -> None:
        self.max_tokens = int(max_tokens)
        self.max_requests = int(max_requests)
        self.max_eur = float(max_eur)
        self._audit = audit or (lambda *a, **kw: None)
        self.state = BudgetState()

    # -------------------------------------------------------------------
    # Factories
    # -------------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        audit: Callable[..., None] | None = None,
    ) -> "BudgetGuard":
        """
        Liest Limits aus ENV:
          BUDGET_MAX_TOKENS     (default 0 = unbegrenzt)
          BUDGET_MAX_REQUESTS   (default 0 = unbegrenzt)
          BUDGET_MAX_EUR        (default 0 = unbegrenzt)
        """

        def _int(key: str) -> int:
            v = os.environ.get(key, "").strip()
            try:
                return int(v) if v else 0
            except ValueError:
                return 0

        def _float(key: str) -> float:
            v = os.environ.get(key, "").strip()
            try:
                return float(v) if v else 0.0
            except ValueError:
                return 0.0

        return cls(
            max_tokens=_int("BUDGET_MAX_TOKENS"),
            max_requests=_int("BUDGET_MAX_REQUESTS"),
            max_eur=_float("BUDGET_MAX_EUR"),
            audit=audit,
        )

    # -------------------------------------------------------------------
    # Usage recording
    # -------------------------------------------------------------------
    def record_usage(
        self,
        *,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Registriert Token-Verbrauch und prueft Limits sofort."""
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        self.state.total_input_tokens += input_tokens
        self.state.total_output_tokens += output_tokens
        self.state.total_requests += 1

        # Kosten
        in_cost, out_cost = _cost_for_model(model)
        spend = (input_tokens / 1_000_000.0) * in_cost + (
            output_tokens / 1_000_000.0
        ) * out_cost
        self.state.total_eur += spend

        # Per-Modell Breakdown
        pm = self.state.per_model.setdefault(
            model or "unknown",
            {"input": 0.0, "output": 0.0, "requests": 0.0, "eur": 0.0},
        )
        pm["input"] += input_tokens
        pm["output"] += output_tokens
        pm["requests"] += 1
        pm["eur"] += spend

        self._check_limits()

    def _check_limits(self) -> None:
        if self.state.breaker_tripped:
            return
        total_tok = self.state.total_input_tokens + self.state.total_output_tokens
        if self.max_tokens and total_tok >= self.max_tokens:
            self._trip(f"tokens: {total_tok}/{self.max_tokens}")
            return
        if self.max_requests and self.state.total_requests >= self.max_requests:
            self._trip(
                f"requests: {self.state.total_requests}/{self.max_requests}"
            )
            return
        if self.max_eur and self.state.total_eur >= self.max_eur:
            self._trip(
                f"spend: {self.state.total_eur:.3f}/{self.max_eur:.2f} EUR"
            )

    def _trip(self, reason: str) -> None:
        self.state.breaker_tripped = True
        self.state.breaker_reason = reason
        self._audit(
            "budget_breaker_tripped",
            reason=reason,
            tokens=self.state.total_input_tokens + self.state.total_output_tokens,
            requests=self.state.total_requests,
            eur=round(self.state.total_eur, 4),
        )

    # -------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------
    def tripped(self) -> bool:
        """True wenn eines der Budgets ueberschritten ist."""
        return self.state.breaker_tripped

    def estimate_spend_eur(self) -> float:
        """Bisheriger Verbrauch in EUR (geschaetzt)."""
        return round(self.state.total_eur, 4)

    def snapshot(self) -> dict[str, Any]:
        """Serialisierbarer Snapshot fuer run_summary.json."""
        total_tok = self.state.total_input_tokens + self.state.total_output_tokens
        return {
            "total_input_tokens": self.state.total_input_tokens,
            "total_output_tokens": self.state.total_output_tokens,
            "total_tokens": total_tok,
            "total_requests": self.state.total_requests,
            "total_eur_estimated": round(self.state.total_eur, 4),
            "per_model": {
                m: {
                    "input": int(d["input"]),
                    "output": int(d["output"]),
                    "requests": int(d["requests"]),
                    "eur": round(d["eur"], 4),
                }
                for m, d in self.state.per_model.items()
            },
            "breaker_tripped": self.state.breaker_tripped,
            "breaker_reason": self.state.breaker_reason,
            "limits": {
                "max_tokens": self.max_tokens or None,
                "max_requests": self.max_requests or None,
                "max_eur": self.max_eur or None,
            },
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    g = BudgetGuard(max_tokens=10_000, max_eur=1.0)
    g.record_usage(model="gpt-4o", input_tokens=2500, output_tokens=500)
    assert not g.tripped(), g.snapshot()
    g.record_usage(model="nvidia/llama-3.2-vision", input_tokens=4000, output_tokens=200)
    assert not g.tripped()
    # Drittes call -> total > 10k Tokens
    g.record_usage(model="gpt-4o-mini", input_tokens=5000, output_tokens=500)
    assert g.tripped(), g.snapshot()
    print("budget_guard self-test ok:", g.snapshot())
