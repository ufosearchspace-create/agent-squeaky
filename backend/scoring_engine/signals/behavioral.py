"""Behavioral signals B1, B2, B3, B5 — rely on hold_time_s from Phase 0.

These read opened_at_ms / closed_at_ms / hold_time_s fields that the
collector now preserves. B3 additionally reads closed_pnl to separate
wins from losses.
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

def _hold_times(trades: Iterable[dict]) -> list[int]:
    """Return non-empty positive hold_time_s values."""
    out: list[int] = []
    for t in trades:
        v = t.get("hold_time_s")
        if v is None:
            opened = t.get("opened_at_ms")
            closed = t.get("closed_at_ms")
            if opened is not None and closed is not None and closed > opened:
                v = (closed - opened) // 1000
        if v is None or v <= 0:
            continue
        out.append(int(v))
    return out


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _log_cv(values: list[int]) -> float:
    """CV of log(values). Returns 0 on empty input."""
    if not values:
        return 0.0
    logs = [math.log(max(v, 1)) for v in values]
    mean = sum(logs) / len(logs)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in logs) / len(logs)
    return math.sqrt(var) / abs(mean)


def _is_bimodal(values: list[int], min_share: float = 0.9) -> bool:
    """Rough bimodal test: at least ``min_share`` of values fall inside
    two tight clusters (where tight = within 20% of cluster centroid).

    Uses a two-pass greedy approach: pick the most frequent value, grow a
    ±20% band, repeat for the residual. Good enough to catch scalp-plus-
    swing bots without spinning up scikit-learn.
    """
    if len(values) < 20:
        return False
    counts = Counter(values)
    centers: list[int] = []
    covered = 0
    residual = list(values)
    for _ in range(2):
        if not residual:
            break
        mode = Counter(residual).most_common(1)[0][0]
        centers.append(mode)
        lo, hi = mode * 0.8, mode * 1.2
        inside = [v for v in residual if lo <= v <= hi]
        covered += len(inside)
        residual = [v for v in residual if not (lo <= v <= hi)]
    return covered / len(values) >= min_share


# ---------------------------------------------------------------------------
# B1 hold_time_variance
# ---------------------------------------------------------------------------

def signal_b1_hold_time_variance(ctx: SignalContext) -> EvidenceScore | None:
    holds = _hold_times(ctx.trades)
    if len(holds) < 20:
        return None

    log_cv = _log_cv(holds)
    bimodal = _is_bimodal(holds)

    if bimodal:
        state = "strong_bot"
    elif log_cv < 0.3:
        state = "medium_bot"
    elif log_cv > 1.2:
        state = "medium_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B1_hold_time_variance",
        log_lr_bits=get_lr("B1_hold_time_variance", state),
        value={
            "log_cv": round(log_cv, 3),
            "bimodal": bimodal,
            "n": len(holds),
        },
        state=state,
        detail=f"log hold_time CV {log_cv:.2f}{' (bimodal)' if bimodal else ''}",
    )


# ---------------------------------------------------------------------------
# B2 hold_time_median
# ---------------------------------------------------------------------------

def signal_b2_hold_time_median(ctx: SignalContext) -> EvidenceScore | None:
    holds = _hold_times(ctx.trades)
    if len(holds) < 10:
        return None

    median = _median([float(h) for h in holds])
    # Boundaries are inclusive with the seed cutoffs (60s scalper, 86400s
    # swing) — an exact 60s or 86400s median already counts as weak_bot.
    if median <= 60 or median >= 86400:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B2_hold_time_median",
        log_lr_bits=get_lr("B2_hold_time_median", state),
        value={"median_s": int(median), "n": len(holds)},
        state=state,
        detail=f"median hold_time {int(median)}s",
    )


# ---------------------------------------------------------------------------
# B3 win_loss_hold_asymmetry
# ---------------------------------------------------------------------------

def signal_b3_win_loss_hold_asymmetry(ctx: SignalContext) -> EvidenceScore | None:
    wins: list[int] = []
    losses: list[int] = []
    for t in ctx.trades:
        h = t.get("hold_time_s")
        if h is None:
            opened = t.get("opened_at_ms")
            closed = t.get("closed_at_ms")
            if opened is not None and closed is not None and closed > opened:
                h = (closed - opened) // 1000
        if h is None or h <= 0:
            continue
        pnl = t.get("closed_pnl") or 0
        if pnl > 0:
            wins.append(int(h))
        elif pnl < 0:
            losses.append(int(h))
    if len(wins) < 10 or len(losses) < 10:
        return None

    med_wins = _median([float(h) for h in wins])
    med_losses = _median([float(h) for h in losses])
    if med_wins <= 0:
        return None
    ratio = med_losses / med_wins

    if ratio >= 2.0:
        state = "strong_human"
    elif ratio >= 1.5:
        state = "medium_human"
    elif 0.9 <= ratio <= 1.1:
        state = "medium_bot"
    elif ratio < 0.7:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B3_win_loss_hold_asymmetry",
        log_lr_bits=get_lr("B3_win_loss_hold_asymmetry", state),
        value={
            "ratio": round(ratio, 3),
            "median_win_s": int(med_wins),
            "median_loss_s": int(med_losses),
            "n_wins": len(wins),
            "n_losses": len(losses),
        },
        state=state,
        detail=f"median_losses/median_wins = {ratio:.2f} ({len(wins)}W/{len(losses)}L)",
    )


# ---------------------------------------------------------------------------
# B5 concurrent_open_positions
# ---------------------------------------------------------------------------

def signal_b5_concurrent_open_positions(ctx: SignalContext) -> EvidenceScore | None:
    intervals = []
    for t in ctx.trades:
        opened = t.get("opened_at_ms")
        closed = t.get("closed_at_ms")
        if opened is None or closed is None or closed <= opened:
            continue
        intervals.append((int(opened), int(closed)))
    if len(intervals) < 10:
        return None

    # Sweep line over open/close events to compute concurrent counts.
    events: list[tuple[int, int]] = []
    for o, c in intervals:
        events.append((o, +1))
        events.append((c, -1))
    events.sort()
    max_concurrent = 0
    cur = 0
    counts_at_events: list[int] = []
    for _, delta in events:
        cur += delta
        counts_at_events.append(cur)
        if cur > max_concurrent:
            max_concurrent = cur
    # Median of concurrent counts sampled at event boundaries.
    med_concurrent = _median([float(c) for c in counts_at_events])

    if max_concurrent >= 10 or med_concurrent >= 5:
        state = "strong_bot"
    elif max_concurrent >= 6 or med_concurrent >= 3:
        state = "medium_bot"
    elif max_concurrent <= 2:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B5_concurrent_open_positions",
        log_lr_bits=get_lr("B5_concurrent_open_positions", state),
        value={
            "max": int(max_concurrent),
            "median": round(med_concurrent, 2),
            "n": len(intervals),
        },
        state=state,
        detail=f"max {max_concurrent} / median {med_concurrent:.1f} concurrent positions",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ALL_BEHAVIORAL_SIGNALS = [
    signal_b1_hold_time_variance,
    signal_b2_hold_time_median,
    signal_b3_win_loss_hold_asymmetry,
    signal_b5_concurrent_open_positions,
]
