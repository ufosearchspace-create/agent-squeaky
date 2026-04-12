"""Classification of a Bayesian posterior into a human-facing label.

With a 95/5 prior, an agent with zero applicable evidence stays at
p_bot = 0.95, which corresponds to LIKELY_BOT. To be classified HUMAN the
agent needs converging evidence totaling at least ~5.1 bits against the
bot hypothesis. See design doc §3.

PR4 adds ``evaluate_human_assisted`` which runs as a parallel evaluator
(not a replacement for ``classify``). The HUMAN_ASSISTED flag indicates
that even though the posterior calls the agent a bot, multiple
psychology signals have independently pointed at human intervention —
the hybrid-agent case.
"""
from __future__ import annotations

from .signals.psychology import PSYCHOLOGY_SIGNAL_NAMES


def classify(p_bot: float) -> str:
    """Map a posterior probability to one of the five classes.

    Thresholds are inclusive on the upper boundary so ``classify(0.97)`` is
    the strongest class (``BOT``) and ``classify(0.30)`` is still
    ``LIKELY_HUMAN`` rather than ``HUMAN``.
    """
    if p_bot >= 0.97:
        return "BOT"
    if p_bot >= 0.85:
        return "LIKELY_BOT"
    if p_bot >= 0.60:
        return "UNCERTAIN"
    if p_bot >= 0.30:
        return "LIKELY_HUMAN"
    return "HUMAN"


# Threshold the posterior must exceed before the HUMAN_ASSISTED check
# runs at all — below this, the Bayesian model has already concluded
# the agent leans human and there is nothing hybrid to surface.
#
# CEK R1 fix: lowered from 0.75 → 0.60 to prevent self-cancellation.
# Psychology signals contribute to the posterior — when they fire
# strongly toward human, they can drag p_bot below the gate and
# suppress the very flag they should trigger. 0.60 catches hybrids
# whose psychology signals contribute -3 to -5 bits without
# accidentally flagging agents the model already calls LIKELY_HUMAN
# (p_bot < 0.30 = HUMAN class, well below this gate).
HUMAN_ASSISTED_MIN_P_BOT = 0.60

# Minimum trade count for psychology signals to be statistically
# meaningful in aggregate. Below this, too much of the signal pool
# returns insufficient-data (None) and the flag would fire on thin
# evidence.
HUMAN_ASSISTED_MIN_TRADES = 30

# A psychology signal counts toward the HA flag when its bits are at
# least this far below zero (i.e. clearly pushing toward human). A
# neutral signal with tiny noise around zero does not qualify.
HUMAN_ASSISTED_BITS_THRESHOLD = -0.5

# Number of psychology signals that must fire to flag the agent.
#
# CEK R6 fix: raised from 2 → 3 to prevent false positives on
# open-source bot frameworks (Freqtrade DCA, Hummingbot). DCA bots
# mechanically produce B7 negative Pearson r (size-up on price drops)
# and S8 round-PnL exits (preset TP/SL). Two signals are too easy
# to trip on clean mechanical strategies; three requires genuine
# multi-dimensional convergence of human-psychology markers.
HUMAN_ASSISTED_MIN_SIGNALS = 3


def evaluate_human_assisted(
    p_bot: float,
    evidence_log: list[dict],
    trade_count: int,
    hard_gates_hit: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Return ``(is_human_assisted, triggered_signals)``.

    Fires when:
      * ``p_bot >= 0.75`` — Bayesian model still leans BOT (otherwise the
        base class already conveys the human lean).
      * ``trade_count >= 30`` — enough data for psychology signals.
      * ``gate:labeled`` NOT in hard_gates_hit — a manual BOT/HUMAN
        label wins outright; HUMAN_ASSISTED is a probabilistic flag and
        must not contradict a reviewer decision.
      * At least two distinct psychology signals have
        ``log_lr_bits <= -0.5`` — conservative confluence requirement
        to filter single-signal noise.

    All other hard gates (persistent_24_7, farm_*, throwaway_farm,
    onchain_human_ceiling) coexist with HUMAN_ASSISTED because a 24/7
    bot can still have a human operator who intervenes, a farm member
    can still be manually steered, and a seasoned owner is the
    textbook profile for someone who knows how to run a hybrid.
    """
    if hard_gates_hit and "gate:labeled" in hard_gates_hit:
        return False, []
    if p_bot < HUMAN_ASSISTED_MIN_P_BOT:
        return False, []
    if trade_count < HUMAN_ASSISTED_MIN_TRADES:
        return False, []
    triggered: list[str] = []
    for e in evidence_log:
        name = e.get("signal")
        bits = e.get("log_lr_bits", 0.0)
        if name in PSYCHOLOGY_SIGNAL_NAMES and bits <= HUMAN_ASSISTED_BITS_THRESHOLD:
            triggered.append(name)
    return len(triggered) >= HUMAN_ASSISTED_MIN_SIGNALS, triggered
