"""Bayesian posterior over P(BOT | evidence), represented in log2-odds.

The posterior is a sum of the prior log-odds (expressed in bits) and the
log-LR bits contributed by each applicable signal. We keep everything in
base-2 so that a bit of evidence corresponds to a 2:1 likelihood ratio,
which makes signal strength intuitive to reason about and to display.
"""
from __future__ import annotations

import math

from .base import EvidenceScore

#: Prior probability that a random DegenClaw agent is a BOT (not a human
#: "cheater"). Chosen from the empirical framing of the arena — it is an AI
#: agent competition, humans are the exceptions. See design doc §2.
PRIOR_P_BOT: float = 0.95

#: Prior in base-2 log-odds form, so the aggregator can simply add
#: per-signal log_lr_bits to it. With P=0.95 this is log2(0.95/0.05) ≈ 4.25.
PRIOR_LOG_ODDS_BITS: float = math.log2(PRIOR_P_BOT / (1.0 - PRIOR_P_BOT))


def posterior(
    evidence: list[EvidenceScore | None],
) -> tuple[float, float, list[dict]]:
    """Aggregate evidence into (p_bot, posterior_log_odds_bits, evidence_log).

    Args:
        evidence: List of EvidenceScore objects, possibly with ``None`` holes
            for signals that did not apply to this agent. ``None`` entries
            are skipped; they are not equivalent to ``log_lr_bits == 0`` in
            the evidence log display.

    Returns:
        Tuple of:
          - ``p_bot``: Posterior probability that the agent is a BOT, clipped
            to [0, 1] by the sigmoid.
          - ``posterior_log_odds_bits``: Prior + sum of contributing log-LRs,
            stored verbatim in scanner_scores.posterior_log_odds for audit.
          - ``evidence_log``: A list of plain dicts suitable for jsonb
            serialization, one entry per contributing signal.
    """
    log_odds = PRIOR_LOG_ODDS_BITS
    log: list[dict] = []
    for e in evidence:
        if e is None:
            continue
        log_odds += e.log_lr_bits
        log.append(
            {
                "signal": e.signal,
                "state": e.state,
                "log_lr_bits": round(e.log_lr_bits, 3),
                "value": e.value,
                "detail": e.detail,
            }
        )
    # Inverse of the base-2 logit: p = 1 / (1 + 2^-x). Numerically stable
    # enough for the magnitudes we care about (|log_odds| < ~60 bits).
    p_bot = 1.0 / (1.0 + 2 ** (-log_odds))
    return p_bot, log_odds, log
