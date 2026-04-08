"""Temporal signals T1..T8.

All signals are pure functions of a SignalContext. They consult the LR
cache (scoring_engine.calibration.get_lr) for bit values tied to the
state they detect, so calibration runs can retune without code changes.

Each signal returns ``None`` when the sample is too small to produce a
meaningful verdict; ``None`` is skipped by the Bayesian aggregator (not
equivalent to neutral).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..base import EvidenceScore, SignalContext
from ..calibration import get_lr


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _group_by_day(trades: list[dict]) -> dict[str, list[dict]]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        ms = t.get("closed_at_ms")
        if ms is None:
            continue
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        by_day[dt.date().isoformat()].append(t)
    return dict(by_day)


def _per_day_hours(day_trades: list[dict]) -> set[int]:
    return {
        datetime.fromtimestamp(t["closed_at_ms"] / 1000, tz=timezone.utc).hour
        for t in day_trades
    }


def _longest_gap(hours: set[int]) -> int:
    """Longest run of consecutive hours missing from ``hours``, 48h wrap."""
    max_g = 0
    g = 0
    for h in range(48):
        if (h % 24) not in hours:
            g += 1
            if g > max_g:
                max_g = g
        else:
            g = 0
    return max_g


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _cv(values: list[float]) -> float:
    """Coefficient of variation; returns 0 when mean is 0."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) / abs(mean)


# ---------------------------------------------------------------------------
# T1 per_day_sleep_gap
# ---------------------------------------------------------------------------

def signal_t1_per_day_sleep_gap(ctx: SignalContext) -> EvidenceScore | None:
    by_day = _group_by_day(ctx.trades)
    valid_days = [_per_day_hours(ts) for ts in by_day.values() if len(ts) >= 3]
    if len(valid_days) < 5:
        return None

    gaps = [_longest_gap(hours) for hours in valid_days]
    gaps_sorted = sorted(gaps)
    median_gap = gaps_sorted[len(gaps_sorted) // 2]
    days_with_8h = sum(1 for g in gaps if g >= 8)
    days_with_3h_or_less = sum(1 for g in gaps if g <= 3)
    n = len(valid_days)

    if median_gap >= 7 and days_with_8h >= 0.7 * n:
        state = "strong_human"
    elif median_gap >= 6:
        state = "medium_human"
    elif median_gap <= 2 and days_with_3h_or_less >= 0.7 * n:
        state = "strong_bot"
    elif median_gap <= 4:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T1_per_day_sleep_gap",
        log_lr_bits=get_lr("T1_per_day_sleep_gap", state),
        value={
            "median_gap_h": median_gap,
            "days_with_8h": days_with_8h,
            "valid_days": n,
        },
        state=state,
        detail=f"median {median_gap}h sleep gap over {n} days, {days_with_8h} with >=8h",
    )


# ---------------------------------------------------------------------------
# T2 sleep_window_stability
# ---------------------------------------------------------------------------

def _sleep_midpoint_hour(hours: set[int]) -> int | None:
    """Midpoint hour (UTC) of the longest gap. None if no clear gap >=3h."""
    all_hours = sorted(hours)
    if len(all_hours) >= 24:
        return None
    max_g = 0
    best_start = 0
    best_len = 0
    for start in range(24):
        length = 0
        h = start
        while (h % 24) not in hours and length < 24:
            length += 1
            h += 1
        if length > best_len:
            best_len = length
            best_start = start
    if best_len < 3:
        return None
    return (best_start + best_len // 2) % 24


def signal_t2_sleep_window_stability(ctx: SignalContext) -> EvidenceScore | None:
    # Only fires when T1 sees a human-like pattern.
    t1 = signal_t1_per_day_sleep_gap(ctx)
    if t1 is None or "human" not in t1.state:
        return None

    by_day = _group_by_day(ctx.trades)
    midpoints: list[int] = []
    for ts in by_day.values():
        if len(ts) < 3:
            continue
        mp = _sleep_midpoint_hour(_per_day_hours(ts))
        if mp is not None:
            midpoints.append(mp)
    if len(midpoints) < 5:
        return None

    # Circular stddev on 24h clock.
    angles = [2 * math.pi * h / 24 for h in midpoints]
    sin_sum = sum(math.sin(a) for a in angles) / len(angles)
    cos_sum = sum(math.cos(a) for a in angles) / len(angles)
    r = math.sqrt(sin_sum ** 2 + cos_sum ** 2)
    circ_std = math.sqrt(-2 * math.log(r)) if 0 < r <= 1 else 2 * math.pi
    stddev_h = circ_std * 24 / (2 * math.pi)

    if stddev_h < 1.5:
        state = "strong_human"
    elif stddev_h < 3.0:
        state = "medium_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T2_sleep_window_stability",
        log_lr_bits=get_lr("T2_sleep_window_stability", state),
        value={"stddev_h": round(stddev_h, 2), "n_days": len(midpoints)},
        state=state,
        detail=f"sleep midpoint circular stddev {stddev_h:.1f}h across {len(midpoints)} days",
    )


# ---------------------------------------------------------------------------
# T3 weekend_weekday_ratio
# ---------------------------------------------------------------------------

def signal_t3_weekend_weekday_ratio(ctx: SignalContext) -> EvidenceScore | None:
    by_day = _group_by_day(ctx.trades)
    if len(by_day) < 7:
        return None

    weekend: list[int] = []
    weekday: list[int] = []
    for day, ts in by_day.items():
        dt = datetime.fromisoformat(day)
        if dt.weekday() >= 5:
            weekend.append(len(ts))
        else:
            weekday.append(len(ts))
    if len(weekend) < 2 or len(weekday) < 3:
        return None

    avg_weekend = sum(weekend) / len(weekend)
    avg_weekday = sum(weekday) / len(weekday)
    if avg_weekday == 0:
        return None
    ratio = avg_weekend / avg_weekday

    if ratio < 0.5:
        state = "medium_human"
    elif ratio < 0.75:
        state = "weak_human"
    elif ratio <= 1.25:
        state = "neutral"
    else:
        state = "weak_bot"

    return EvidenceScore(
        signal="T3_weekend_weekday_ratio",
        log_lr_bits=get_lr("T3_weekend_weekday_ratio", state),
        value={"ratio": round(ratio, 3), "weekend_days": len(weekend), "weekday_days": len(weekday)},
        state=state,
        detail=f"weekend/weekday volume ratio {ratio:.2f}",
    )


# ---------------------------------------------------------------------------
# T4 daily_volume_cv
# ---------------------------------------------------------------------------

def signal_t4_daily_volume_cv(ctx: SignalContext) -> EvidenceScore | None:
    by_day = _group_by_day(ctx.trades)
    if len(by_day) < 7:
        return None
    counts = [float(len(ts)) for ts in by_day.values()]
    cv = _cv(counts)

    if cv < 0.15:
        state = "strong_bot"
    elif cv < 0.3:
        state = "medium_bot"
    elif cv > 1.0:
        state = "medium_human"
    elif cv > 0.6:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T4_daily_volume_cv",
        log_lr_bits=get_lr("T4_daily_volume_cv", state),
        value={"cv": round(cv, 3), "days": len(counts)},
        state=state,
        detail=f"daily volume cv {cv:.2f} across {len(counts)} days",
    )


# ---------------------------------------------------------------------------
# T5 dead_days
# ---------------------------------------------------------------------------

def signal_t5_dead_days(ctx: SignalContext) -> EvidenceScore | None:
    by_day = _group_by_day(ctx.trades)
    if len(by_day) < 3:
        return None
    dates = sorted(datetime.fromisoformat(d).date() for d in by_day.keys())
    first, last = dates[0], dates[-1]
    total_span = (last - first).days + 1
    if total_span < 10:
        return None
    # Number of dates inside [first,last] with zero trades.
    active_ordinals = {d.toordinal() for d in dates}
    first_ord = first.toordinal()
    dead = sum(1 for i in range(total_span) if (first_ord + i) not in active_ordinals)

    if dead >= 2:
        state = "medium_human"
    elif dead == 1:
        state = "weak_human"
    elif dead == 0 and total_span >= 14:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T5_dead_days",
        log_lr_bits=get_lr("T5_dead_days", state),
        value={"dead_days": dead, "total_span_days": total_span},
        state=state,
        detail=f"{dead} dead days in a {total_span}-day window",
    )


# ---------------------------------------------------------------------------
# T6 intraday_burst_score
# ---------------------------------------------------------------------------

def signal_t6_intraday_burst_score(ctx: SignalContext) -> EvidenceScore | None:
    if len(ctx.trades) < 20:
        return None
    ts = sorted(t["closed_at_ms"] for t in ctx.trades if t.get("closed_at_ms"))
    burst_members = 0
    i = 0
    while i < len(ts):
        j = i
        while j + 1 < len(ts) and ts[j + 1] - ts[i] <= 5 * 60_000:
            j += 1
        cluster_size = j - i + 1
        if cluster_size >= 3:
            burst_members += cluster_size
            i = j + 1
        else:
            i += 1
    burst_pct = burst_members / len(ts)

    if burst_pct > 0.6:
        state = "medium_bot"
    elif burst_pct > 0.4:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T6_intraday_burst_score",
        log_lr_bits=get_lr("T6_intraday_burst_score", state),
        value={"burst_pct": round(burst_pct, 3), "n_trades": len(ts)},
        state=state,
        detail=f"{burst_pct * 100:.0f}% of trades in 5-minute 3+ bursts",
    )


# ---------------------------------------------------------------------------
# T7 per_day_interval_cv
# ---------------------------------------------------------------------------

def signal_t7_per_day_interval_cv(ctx: SignalContext) -> EvidenceScore | None:
    by_day = _group_by_day(ctx.trades)
    per_day_cvs: list[float] = []
    for ts in by_day.values():
        if len(ts) < 5:
            continue
        stamps = sorted(t["closed_at_ms"] for t in ts)
        intervals = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
        per_day_cvs.append(_cv([float(x) for x in intervals]))
    if len(per_day_cvs) < 5:
        return None
    median_cv = _median(per_day_cvs)

    if median_cv < 0.3:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T7_per_day_interval_cv",
        log_lr_bits=get_lr("T7_per_day_interval_cv", state),
        value={"median_cv": round(median_cv, 3), "days": len(per_day_cvs)},
        state=state,
        detail=f"median per-day interval cv {median_cv:.2f}",
    )


# ---------------------------------------------------------------------------
# T8 ms_entropy
# ---------------------------------------------------------------------------

def signal_t8_ms_entropy(ctx: SignalContext) -> EvidenceScore | None:
    if len(ctx.trades) < 20:
        return None
    ms_parts = [t["closed_at_ms"] % 1000 for t in ctx.trades if t.get("closed_at_ms") is not None]
    if not ms_parts:
        return None
    zero_pct = sum(1 for m in ms_parts if m == 0) / len(ms_parts)

    counts = Counter(ms_parts)
    total = sum(counts.values())
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)

    if zero_pct > 0.8:
        state = "strong_bot"
    elif entropy < 3.0:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T8_ms_entropy",
        log_lr_bits=get_lr("T8_ms_entropy", state),
        value={"zero_pct": round(zero_pct, 3), "entropy_bits": round(entropy, 2)},
        state=state,
        detail=f"ms entropy {entropy:.1f} bits, {zero_pct * 100:.0f}% on .000",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ALL_TEMPORAL_SIGNALS = [
    signal_t1_per_day_sleep_gap,
    signal_t2_sleep_window_stability,
    signal_t3_weekend_weekday_ratio,
    signal_t4_daily_volume_cv,
    signal_t5_dead_days,
    signal_t6_intraday_burst_score,
    signal_t7_per_day_interval_cv,
    signal_t8_ms_entropy,
]
