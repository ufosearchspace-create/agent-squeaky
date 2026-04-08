"""Core types for the Bayesian scoring engine.

Each signal module exports a pure function that takes a SignalContext and
returns either None (insufficient data — not counted in posterior) or an
EvidenceScore contributing log2-LR bits toward or away from the BOT
hypothesis.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class EvidenceScore:
    """One unit of Bayesian evidence produced by a signal module.

    Attributes:
        signal: Stable identifier, e.g. "T1_per_day_sleep_gap".
        log_lr_bits: log2(P(observed | BOT) / P(observed | HUMAN)).
            Positive values move the posterior toward BOT, negative toward
            HUMAN. The magnitude is capped in practice around ±5 bits.
        value: Raw measurement(s) used to choose the state. Stored as-is
            in the evidence_log column so we can reproduce decisions later.
        state: Named bucket the measurement fell into (e.g. "strong_bot").
            Drives the LR lookup via calibration.get_lr(signal, state).
        detail: One-line human-readable explanation for display.
    """

    signal: str
    log_lr_bits: float
    value: Any
    state: str
    detail: str


@dataclass
class SignalContext:
    """Everything a pure signal function needs to produce evidence.

    Populated once per agent by analyzer.score_agent and passed to every
    registered signal. Signals MUST NOT perform IO — the context is the
    entire world they see.
    """

    agent: dict
    trades: list[dict]
    candles: dict[str, list]
    onchain: Optional[dict]
    now_ms: int
    owner_cluster: list[dict] = field(default_factory=list)


# Type alias for signal module registration.
SignalFn = Callable[[SignalContext], Optional[EvidenceScore]]
