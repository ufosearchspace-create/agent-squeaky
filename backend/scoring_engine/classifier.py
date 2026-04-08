"""Classification of a Bayesian posterior into a human-facing label.

With a 95/5 prior, an agent with zero applicable evidence stays at
p_bot = 0.95, which corresponds to LIKELY_BOT. To be classified HUMAN the
agent needs converging evidence totaling at least ~5.1 bits against the
bot hypothesis. See design doc §3.
"""
from __future__ import annotations


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
