"""Structural signals S1..S7.

These look at *what* the agent trades (size, coin, leverage) rather than
*when*. They are relatively easy to fake individually but cheap to compute
and often caught by converging evidence with the harder-to-fake temporal
and behavioral signals.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

from ..base import EvidenceScore, SignalContext
from ..calibration import get_lr


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sizes(trades: Iterable[dict]) -> list[float]:
    out: list[float] = []
    for t in trades:
        v = t.get("position_size")
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _is_round_size(s: float) -> bool:
    """Human-preferred round sizes: multiples of {10,25,50,100,250,500}
    or simple integers up to 1000."""
    if s <= 0:
        return False
    for div in (500, 250, 100, 50, 25, 10):
        if s % div == 0:
            return True
    if s == int(s) and s <= 1000:
        return True
    return False


def _decimal_count(s: float) -> int:
    """Number of non-zero decimal places in a float's str representation."""
    text = repr(s)
    if "." not in text:
        return 0
    # repr drops trailing zeros, so the length after "." is a fair proxy for
    # the "meaningful" decimals. Cap at 10 to avoid absurd values.
    return min(len(text.split(".")[-1]), 10)


# ---------------------------------------------------------------------------
# S1 round_size_pct
# ---------------------------------------------------------------------------

def signal_s1_round_size_pct(ctx: SignalContext) -> EvidenceScore | None:
    sizes = _sizes(ctx.trades)
    if len(sizes) < 5:
        return None
    round_pct = sum(1 for s in sizes if _is_round_size(s)) / len(sizes)

    if round_pct > 0.4:
        state = "medium_human"
    elif round_pct > 0.2:
        state = "weak_human"
    elif round_pct < 0.05:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S1_round_size_pct",
        log_lr_bits=get_lr("S1_round_size_pct", state),
        value={"round_pct": round(round_pct, 3), "n": len(sizes)},
        state=state,
        detail=f"{round_pct * 100:.0f}% round-number sizes across {len(sizes)} trades",
    )


# ---------------------------------------------------------------------------
# S2 size_decimal_precision
# ---------------------------------------------------------------------------

def signal_s2_size_decimal_precision(ctx: SignalContext) -> EvidenceScore | None:
    sizes = _sizes(ctx.trades)
    if len(sizes) < 5:
        return None
    avg_decimals = sum(_decimal_count(s) for s in sizes) / len(sizes)

    if avg_decimals >= 5:
        state = "strong_bot"
    elif avg_decimals >= 4:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S2_size_decimal_precision",
        log_lr_bits=get_lr("S2_size_decimal_precision", state),
        value={"avg_decimals": round(avg_decimals, 2), "n": len(sizes)},
        state=state,
        detail=f"average {avg_decimals:.1f} decimal places in position_size",
    )


# ---------------------------------------------------------------------------
# S3 benford_compliance
# ---------------------------------------------------------------------------

_BENFORD_EXPECTED = [math.log10(1 + 1 / d) for d in range(1, 10)]


def signal_s3_benford_compliance(ctx: SignalContext) -> EvidenceScore | None:
    sizes = _sizes(ctx.trades)
    if len(sizes) < 30:
        return None
    # Leading digit of |size|. Ignore zeros.
    leading: list[int] = []
    for s in sizes:
        if s <= 0:
            continue
        # Normalize to 1..10 range to find the first significant digit.
        while s < 1:
            s *= 10
        while s >= 10:
            s /= 10
        leading.append(int(s))
    if len(leading) < 30:
        return None

    # scipy.stats.chisquare is a hard dep (in requirements.txt). If it fails
    # on pathological input (e.g. a leading-digit bin with zero expected
    # count), we abstain rather than returning a fake p-value — the manual
    # math.exp(-chi/2) fallback used here previously is NOT a valid
    # chi-square CDF inversion and would misclassify edge cases as bots.
    try:
        from scipy.stats import chisquare

        exp_counts = [p * len(leading) for p in _BENFORD_EXPECTED]
        obs_counts = [leading.count(d) for d in range(1, 10)]
        if min(exp_counts) <= 0:
            return None
        _stat, p_value = chisquare(obs_counts, f_exp=exp_counts)
    except Exception:
        return None

    if p_value < 0.05:
        state = "medium_bot"
    elif p_value > 0.3:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S3_benford_compliance",
        log_lr_bits=get_lr("S3_benford_compliance", state),
        value={"p_value": round(float(p_value), 4), "n": len(leading)},
        state=state,
        detail=f"Benford chi-square p={p_value:.3f} over {len(leading)} leading digits",
    )


# ---------------------------------------------------------------------------
# S4 coin_diversity
# ---------------------------------------------------------------------------

def signal_s4_coin_diversity(ctx: SignalContext) -> EvidenceScore | None:
    coins = [t.get("coin") for t in ctx.trades if t.get("coin")]
    if len(coins) < 10:
        return None
    unique = set(coins)
    n_unique = len(unique)
    ratio = n_unique / len(coins)

    if n_unique >= 15 and ratio > 0.2:
        state = "strong_bot"
    elif n_unique >= 10:
        state = "medium_bot"
    elif n_unique <= 3 and len(coins) >= 20:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S4_coin_diversity",
        log_lr_bits=get_lr("S4_coin_diversity", state),
        value={"unique_coins": n_unique, "ratio": round(ratio, 3), "n": len(coins)},
        state=state,
        detail=f"{n_unique} unique coins across {len(coins)} trades",
    )


# ---------------------------------------------------------------------------
# S5 size_ladder_pattern
# ---------------------------------------------------------------------------

def signal_s5_size_ladder_pattern(ctx: SignalContext) -> EvidenceScore | None:
    sizes = _sizes(ctx.trades)
    if len(sizes) < 20:
        return None
    # Cluster sizes at 0.5% precision to detect "fixed ladder" patterns.
    # Two sizes are considered the same bucket when rounded to 3 significant
    # figures match.
    def bucket(s: float) -> float:
        if s == 0:
            return 0.0
        mag = 10 ** (math.floor(math.log10(abs(s))) - 2)
        return round(s / mag) * mag

    counts = Counter(bucket(s) for s in sizes)
    top3_share = sum(c for _, c in counts.most_common(3)) / len(sizes)

    if top3_share > 0.7:
        state = "strong_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S5_size_ladder_pattern",
        log_lr_bits=get_lr("S5_size_ladder_pattern", state),
        value={"top3_share": round(top3_share, 3), "buckets": len(counts)},
        state=state,
        detail=f"top-3 size buckets cover {top3_share * 100:.0f}% of trades",
    )


# ---------------------------------------------------------------------------
# S6 identical_size_repetition
# ---------------------------------------------------------------------------

def signal_s6_identical_size_repetition(ctx: SignalContext) -> EvidenceScore | None:
    sizes = _sizes(ctx.trades)
    if len(sizes) < 10:
        return None
    counts = Counter(sizes)
    max_freq = max(counts.values()) / len(sizes)
    unique_ratio = len(counts) / len(sizes)

    if max_freq > 0.5:
        state = "strong_bot"
    elif max_freq > 0.3:
        state = "medium_bot"
    elif unique_ratio > 0.95 and len(sizes) >= 30:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S6_identical_size_repetition",
        log_lr_bits=get_lr("S6_identical_size_repetition", state),
        value={
            "max_freq": round(max_freq, 3),
            "unique_ratio": round(unique_ratio, 3),
            "n": len(sizes),
        },
        state=state,
        detail=f"max single-size frequency {max_freq * 100:.0f}%, unique ratio {unique_ratio:.2f}",
    )


# ---------------------------------------------------------------------------
# S7 leverage_variance
# ---------------------------------------------------------------------------

def signal_s7_leverage_variance(ctx: SignalContext) -> EvidenceScore | None:
    levs = [t.get("leverage") for t in ctx.trades if t.get("leverage") is not None]
    if len(levs) < 15:
        return None
    counts = Counter(levs)
    distinct = len(counts)
    total = sum(counts.values())
    dominant = max(counts.values()) / total
    # Shannon entropy in bits of the leverage distribution.
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)

    if distinct == 1:
        state = "medium_bot"
    elif distinct == 2 and dominant > 0.9:
        state = "weak_bot"
    elif distinct >= 4 and entropy > 1.5:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S7_leverage_variance",
        log_lr_bits=get_lr("S7_leverage_variance", state),
        value={
            "distinct": distinct,
            "dominant": round(dominant, 3),
            "entropy_bits": round(entropy, 2),
            "n": total,
        },
        state=state,
        detail=f"{distinct} distinct leverage values, entropy {entropy:.2f} bits",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ALL_STRUCTURAL_SIGNALS = [
    signal_s1_round_size_pct,
    signal_s2_size_decimal_precision,
    signal_s3_benford_compliance,
    signal_s4_coin_diversity,
    signal_s5_size_ladder_pattern,
    signal_s6_identical_size_repetition,
    signal_s7_leverage_variance,
]
