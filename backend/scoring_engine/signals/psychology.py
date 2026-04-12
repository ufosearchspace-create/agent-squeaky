"""Psychology signals (PR4) — empirical trading-psychology markers.

These signals target HYBRID agents: bots with occasional human
intervention. A pure Bayesian aggregate over 26 mechanical signals
tends to label hybrids as BOT because the few human-touch trades get
averaged out. These 8 signals look for specific fingerprints of human
cognition that a pure bot cannot fake without being explicitly
programmed to.

Academic grounding:

* B6 disposition_effect       — Shefrin & Statman (1985); Odean (1998)
* B7 loss_chase_sizing        — Kahneman & Tversky (1979), prospect theory
* B8 hot_hand_tempo           — Gilovich, Vallone, Tversky (1985)
* B9 tilt_spike               — Lo, Repin, Steenbarger (2005)
* S8 round_pnl_exits          — Harris (1991); Bhattacharya et al. (2012)
* S9 anchor_exits             — Tversky & Kahneman (1974), anchoring bias
* T9 gap_entropy              — circadian-rhythm research (Czeisler et al.)
* B10 intraday_emotion_shape  — retail U-shape intraday activity

Trigger threshold convention: every signal needs a sensible minimum
trade count to avoid noise. Below the minimum the signal returns
``None`` (same pattern as B1/B2/B3/B5/T1 etc.) and does not contribute
to the posterior. The ``evaluate_human_assisted`` classifier helper
further requires >=2 of these signals to fire with bits <= -0.5 before
flagging the agent — single-signal trips are treated as noise.

Every signal is a pure function of ``SignalContext``. No IO, no global
state. Determinism is required so rescoring reproduces past verdicts.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from bisect import bisect_right

from ..base import EvidenceScore, SignalContext
from ..calibration import get_lr


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation. Returns 0 on degenerate input (empty, zero var)."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return 0.0
    mx = _mean(xs)
    my = _mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sx * sy)


def _closed_pnl_trades(trades: Iterable[dict]) -> list[dict]:
    """Return trades that have a usable closed_pnl value."""
    out = []
    for t in trades:
        pnl = t.get("closed_pnl")
        if pnl is None:
            continue
        out.append(t)
    return out


def _sorted_by_close(trades: Iterable[dict]) -> list[dict]:
    """Return trades ordered by closed_at_ms ascending, skipping nulls."""
    out = [t for t in trades if t.get("closed_at_ms") is not None]
    out.sort(key=lambda t: t["closed_at_ms"])
    return out


def _hold_time_s(t: dict) -> int | None:
    """Pull hold_time_s, falling back to opened/closed timestamp diff."""
    v = t.get("hold_time_s")
    if v is not None and v > 0:
        return int(v)
    opened = t.get("opened_at_ms")
    closed = t.get("closed_at_ms")
    if opened is not None and closed is not None and closed > opened:
        return int((closed - opened) // 1000)
    return None


# ---------------------------------------------------------------------------
# B6 disposition_effect
# ---------------------------------------------------------------------------
# Shefrin & Statman 1985: humans realize gains too quickly and hold losses
# too long. Measured as median(loss_hold) / median(win_hold). Values well
# above 1.0 indicate emotional attachment to losing positions — a bot
# following mechanical exit rules should produce a ratio close to 1.0.
# Inverse case (ratio << 1) is also suspicious — it points to a rigid
# profit-take-and-hold-losers bot that is still clearly mechanical.

def signal_b6_disposition_effect(ctx: SignalContext) -> EvidenceScore | None:
    wins: list[int] = []
    losses: list[int] = []
    for t in ctx.trades:
        pnl = t.get("closed_pnl")
        if pnl is None:
            continue
        h = _hold_time_s(t)
        if h is None or h <= 0:
            continue
        if pnl > 0:
            wins.append(h)
        elif pnl < 0:
            losses.append(h)
    # Needs enough of both sides to be statistically meaningful. The
    # threshold (5/5) is deliberately lower than B3's 10/10 because B6
    # treats weaker evidence toward human, while B3 looks for strong
    # asymmetry as a bot-vs-human discriminator.
    if len(wins) < 5 or len(losses) < 5:
        return None
    med_win = _median([float(x) for x in wins])
    med_loss = _median([float(x) for x in losses])
    if med_win <= 0:
        return None
    ratio = med_loss / med_win

    if ratio >= 2.0:
        state = "strong_human"
    elif ratio >= 1.3:
        state = "medium_human"
    elif 0.8 <= ratio <= 1.3:
        state = "neutral"
    elif ratio < 0.5:
        # Holds winners dramatically longer than losers — extremely
        # rigid rule-based exit logic, not human psychology.
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B6_disposition_effect",
        log_lr_bits=get_lr("B6_disposition_effect", state),
        value={
            "ratio": round(ratio, 3),
            "median_win_s": int(med_win),
            "median_loss_s": int(med_loss),
            "n_wins": len(wins),
            "n_losses": len(losses),
        },
        state=state,
        detail=f"loss/win hold ratio {ratio:.2f} ({len(wins)}W/{len(losses)}L)",
    )


# ---------------------------------------------------------------------------
# B7 loss_chase_sizing
# ---------------------------------------------------------------------------
# Kahneman & Tversky 1979: after losses, humans feel compelled to
# "recover" and increase position size. Bots with constant sizing rules
# show zero correlation between prior-PnL and next-trade size. A
# negative Pearson r between rolling_pnl(t-5..t-1) and size(t) indicates
# "size up after losing streak" — the classic chase.

def signal_b7_loss_chase_sizing(ctx: SignalContext) -> EvidenceScore | None:
    ordered = _sorted_by_close(ctx.trades)
    if len(ordered) < 30:
        return None
    rolling: list[float] = []
    sizes: list[float] = []
    for i in range(5, len(ordered)):
        window_pnls = [
            float(ordered[j].get("closed_pnl") or 0.0)
            for j in range(i - 5, i)
        ]
        size = ordered[i].get("position_size")
        if size is None:
            continue
        rolling.append(sum(window_pnls))
        sizes.append(float(size))
    if len(rolling) < 20:
        return None
    r = _pearson(rolling, sizes)

    # Negative r => size goes UP when recent PnL is DOWN => chase.
    if r <= -0.30:
        state = "strong_human"
    elif r <= -0.15:
        state = "medium_human"
    elif r >= 0.15:
        # Positive r => size goes UP after wins. Consistent with
        # confident-human-on-a-roll OR pyramiding bot. We treat it as
        # weakly human because pyramiding bots are rare in this arena.
        state = "weak_human"
    elif -0.05 <= r <= 0.05:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B7_loss_chase_sizing",
        log_lr_bits=get_lr("B7_loss_chase_sizing", state),
        value={"pearson_r": round(r, 3), "n_pairs": len(rolling)},
        state=state,
        detail=f"rolling-PnL × size r = {r:+.2f} (n={len(rolling)})",
    )


# ---------------------------------------------------------------------------
# B8 hot_hand_tempo
# ---------------------------------------------------------------------------
# Gilovich, Vallone, Tversky 1985 "hot hand fallacy" applied to trading:
# after a win streak humans accelerate ("I'm on fire"), after losses they
# slow down. Bots have constant tempo independent of recent outcomes.
# Measured as Pearson correlation between rolling trade-rate and trailing
# win-rate across sliding windows.

def signal_b8_hot_hand_tempo(ctx: SignalContext) -> EvidenceScore | None:
    ordered = _sorted_by_close(ctx.trades)
    if len(ordered) < 30:
        return None
    # Compute per-5-trade-window: trades/hour pace, trailing win_rate.
    win_rates: list[float] = []
    rates: list[float] = []
    window = 5
    for i in range(window, len(ordered)):
        prev_slice = ordered[i - window: i]
        cur_slice = ordered[i: i + window]
        if len(cur_slice) < 2:
            break
        # Trailing window win rate.
        wins = sum(
            1 for t in prev_slice if (t.get("closed_pnl") or 0) > 0
        )
        win_rates.append(wins / window)
        # Current-window pace: trades per hour over the span it covers.
        span_ms = (
            cur_slice[-1]["closed_at_ms"] - cur_slice[0]["closed_at_ms"]
        )
        if span_ms <= 0:
            rates.append(0.0)
        else:
            rates.append(len(cur_slice) / (span_ms / 3_600_000.0))
    if len(rates) < 10:
        return None
    r = _pearson(win_rates, rates)

    if r >= 0.35:
        state = "strong_human"
    elif r >= 0.15:
        state = "medium_human"
    elif abs(r) < 0.10:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B8_hot_hand_tempo",
        log_lr_bits=get_lr("B8_hot_hand_tempo", state),
        value={"pearson_r": round(r, 3), "n_windows": len(rates)},
        state=state,
        detail=f"win-rate × pace r = {r:+.2f}",
    )


# ---------------------------------------------------------------------------
# B9 tilt_spike
# ---------------------------------------------------------------------------
# Lo, Repin, Steenbarger 2005: after a "big loss" (>1σ) humans enter a
# 30-60min window of elevated activity ("revenge trade") or, inversely,
# freeze out of fear. Bots maintain baseline tempo regardless of prior
# outcome. Metric: ratio of trade rate in the 30 min after a big loss
# vs baseline rate.

def signal_b9_tilt_spike(ctx: SignalContext) -> EvidenceScore | None:
    ordered = _sorted_by_close(ctx.trades)
    if len(ordered) < 30:
        return None
    pnls = [float(t.get("closed_pnl") or 0.0) for t in ordered]
    # Only losses matter for the σ threshold.
    losses_only = [p for p in pnls if p < 0]
    if len(losses_only) < 3:
        return None
    loss_std = _stddev(losses_only)
    if loss_std <= 0:
        return None
    mean_loss = _mean(losses_only)
    big_loss_cutoff = mean_loss - loss_std  # more negative than 1σ

    # Find big-loss events and count trades within 30 min after each.
    post_window_ms = 30 * 60 * 1000
    total_trades = len(ordered)
    # Baseline rate: trades per 30-min across total span.
    first_ms = ordered[0]["closed_at_ms"]
    last_ms = ordered[-1]["closed_at_ms"]
    total_span = last_ms - first_ms
    if total_span <= 0:
        return None
    baseline_per_30m = total_trades / (total_span / post_window_ms)
    if baseline_per_30m <= 0:
        return None

    big_loss_events: list[int] = []
    for i, t in enumerate(ordered):
        if pnls[i] < big_loss_cutoff:
            big_loss_events.append(t["closed_at_ms"])
    if len(big_loss_events) < 3:
        return None

    # CEK C1 fix: use bisect on the sorted close-time list instead of
    # a nested linear scan. O(n log n) total vs O(n × events).
    closes = [t["closed_at_ms"] for t in ordered]
    post_counts: list[int] = []
    for ev_ms in big_loss_events:
        lo = bisect_right(closes, ev_ms)
        hi = bisect_right(closes, ev_ms + post_window_ms)
        post_counts.append(hi - lo)
    avg_post = _mean([float(c) for c in post_counts])
    ratio = avg_post / baseline_per_30m

    if ratio >= 2.0:
        state = "strong_human"  # revenge tilt
    elif ratio >= 1.3:
        state = "medium_human"
    elif ratio <= 0.4:
        state = "medium_human"  # fear freeze also points to human
    elif 0.8 <= ratio <= 1.2:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B9_tilt_spike",
        log_lr_bits=get_lr("B9_tilt_spike", state),
        value={
            "ratio": round(ratio, 3),
            "events": len(big_loss_events),
            "baseline_per_30m": round(baseline_per_30m, 3),
        },
        state=state,
        detail=f"post-big-loss rate ratio {ratio:.2f} ({len(big_loss_events)} events)",
    )


# ---------------------------------------------------------------------------
# S8 round_pnl_exits
# ---------------------------------------------------------------------------
# Harris 1991: humans exit on round-number PnL (+$100, -$50, +$500). Bots
# exit on algorithmic levels (trailing stop, fib, ATR multiples). Metric:
# what fraction of exit PnL values fall within ±5% of a round multiple
# (10, 25, 50, 100, 250, 500)?

_ROUND_MULTIPLES = (10.0, 25.0, 50.0, 100.0, 250.0, 500.0)


def _is_near_round(pnl: float, tolerance_pct: float = 0.05) -> bool:
    """True if |pnl| is within tolerance of a round multiple."""
    absv = abs(pnl)
    if absv < 1:
        return False
    for mult in _ROUND_MULTIPLES:
        # Check near positive multiples of ``mult`` up to 5000.
        for k in range(1, int(5000 / mult) + 1):
            target = mult * k
            if target > 10000:
                break
            if abs(absv - target) <= target * tolerance_pct:
                return True
    return False


def signal_s8_round_pnl_exits(ctx: SignalContext) -> EvidenceScore | None:
    pnls = [
        float(t.get("closed_pnl") or 0.0)
        for t in ctx.trades
        if t.get("closed_pnl") is not None
    ]
    if len(pnls) < 20:
        return None
    near_round = sum(1 for p in pnls if _is_near_round(p))
    pct = near_round / len(pnls)

    # A uniform distribution would expect ~30% near-round under a 5%
    # tolerance band across 6 multiples — the human signal kicks in well
    # above that.
    if pct >= 0.55:
        state = "strong_human"
    elif pct >= 0.42:
        state = "medium_human"
    elif pct <= 0.20:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S8_round_pnl_exits",
        log_lr_bits=get_lr("S8_round_pnl_exits", state),
        value={
            "pct_near_round": round(pct, 3),
            "n_trades": len(pnls),
            "n_near_round": near_round,
        },
        state=state,
        detail=f"{pct * 100:.0f}% of exits on round-number PnL",
    )


# ---------------------------------------------------------------------------
# S9 anchor_exits
# ---------------------------------------------------------------------------
# Tversky & Kahneman 1974: humans anchor on entry price and exit at
# round percentage returns (+1%, +2%, -5%, -10%). Bots exit on market
# structure, not anchor-relative levels. Metric: fraction of exits
# within ±0.15% of a round-percent target.

_ROUND_PERCENTS = (0.01, 0.02, 0.025, 0.05, 0.075, 0.10, 0.15, 0.20)


def signal_s9_anchor_exits(ctx: SignalContext) -> EvidenceScore | None:
    returns: list[float] = []
    for t in ctx.trades:
        entry = t.get("entry_price")
        exit_ = t.get("exit_price")
        if entry is None or exit_ is None or entry <= 0:
            continue
        direction = (t.get("direction") or "LONG").upper()
        if direction == "SHORT":
            ret = (entry - exit_) / entry
        else:
            ret = (exit_ - entry) / entry
        returns.append(ret)
    if len(returns) < 20:
        return None

    tolerance = 0.0015  # 0.15 percentage points absolute
    near_anchor = 0
    for r in returns:
        absr = abs(r)
        if absr < 0.005:  # below 0.5% — dominated by fees, not anchoring
            continue
        for target in _ROUND_PERCENTS:
            if abs(absr - target) <= tolerance:
                near_anchor += 1
                break
    pct = near_anchor / len(returns)

    # Baseline of a uniform return distribution would give ~15-20% near
    # any of these anchors given the 0.15pp tolerance. Human signal
    # starts ~30%.
    if pct >= 0.45:
        state = "strong_human"
    elif pct >= 0.30:
        state = "medium_human"
    elif pct <= 0.12:
        state = "medium_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="S9_anchor_exits",
        log_lr_bits=get_lr("S9_anchor_exits", state),
        value={
            "pct_near_anchor": round(pct, 3),
            "n_trades": len(returns),
            "n_near_anchor": near_anchor,
        },
        state=state,
        detail=f"{pct * 100:.0f}% of exits on round-% returns",
    )


# ---------------------------------------------------------------------------
# T9 gap_entropy
# ---------------------------------------------------------------------------
# Humans have circadian rhythms — regular sleep gaps at predictable
# hours. Bots either have no gaps (24/7) or have predictable scheduled
# gaps (maintenance windows). Hybrid agents have neither: irregular gaps
# caused by human intervention (the human pauses the bot, does something
# manually, restarts). Measured via Shannon entropy of gap START hours.

def signal_t9_gap_entropy(ctx: SignalContext) -> EvidenceScore | None:
    ordered = _sorted_by_close(ctx.trades)
    if len(ordered) < 30:
        return None
    # Need at least a week of history for gap patterns to be meaningful.
    span_ms = ordered[-1]["closed_at_ms"] - ordered[0]["closed_at_ms"]
    if span_ms < 7 * 24 * 3600 * 1000:
        return None

    gap_start_hours: list[int] = []
    gap_count = 0
    for i in range(1, len(ordered)):
        diff_ms = ordered[i]["closed_at_ms"] - ordered[i - 1]["closed_at_ms"]
        if diff_ms < 2 * 3600 * 1000:  # only gaps >=2h count
            continue
        gap_count += 1
        dt = datetime.fromtimestamp(
            ordered[i - 1]["closed_at_ms"] / 1000, tz=timezone.utc
        )
        gap_start_hours.append(dt.hour)

    if gap_count < 5:
        # Agent runs essentially 24/7 — bot-like. Let T1/T2 handle the
        # sleep-window call; T9 stays neutral for this case because a
        # 24/7 bot may also be a hybrid whose operator never pauses it.
        return EvidenceScore(
            signal="T9_gap_entropy",
            log_lr_bits=get_lr("T9_gap_entropy", "no_gaps"),
            value={"gap_count": gap_count},
            state="no_gaps",
            detail=f"{gap_count} gaps ≥2h (bot-like continuous activity)",
        )

    counter = Counter(gap_start_hours)
    probs = [c / gap_count for c in counter.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)

    # Low entropy = gaps cluster at specific hours = circadian (human).
    # Very high entropy = gaps spread across all hours = chaotic (hybrid).
    # Medium entropy = mix, inconclusive.
    if entropy <= 1.5:
        state = "medium_human"  # tight circadian cluster
    elif entropy >= 3.0:
        state = "weak_human"  # chaotic — hybrid marker
    else:
        state = "neutral"

    return EvidenceScore(
        signal="T9_gap_entropy",
        log_lr_bits=get_lr("T9_gap_entropy", state),
        value={
            "entropy_bits": round(entropy, 3),
            "gap_count": gap_count,
            "distinct_hours": len(counter),
        },
        state=state,
        detail=f"gap-start entropy {entropy:.2f} bits ({gap_count} gaps)",
    )


# ---------------------------------------------------------------------------
# B10 intraday_emotion_shape
# ---------------------------------------------------------------------------
# Retail traders cluster at market open (first crypto volatility) and
# close/end-of-day emotional exits, giving a U-shape. Bots give a flat
# distribution. Metric: is the hourly trade distribution closer to flat
# (bot) or U-shaped (human)?

def signal_b10_intraday_emotion_shape(ctx: SignalContext) -> EvidenceScore | None:
    if len(ctx.trades) < 50:
        return None
    hours = [0] * 24
    for t in ctx.trades:
        ts = t.get("closed_at_ms")
        if ts is None:
            continue
        h = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour
        hours[h] += 1
    total = sum(hours)
    if total == 0:
        return None
    probs = [h / total for h in hours]

    # Coefficient of variation: very low = flat (bot), higher = shaped.
    mean_p = _mean(probs)
    std_p = _stddev(probs)
    cv = std_p / mean_p if mean_p > 0 else 0.0

    # Count hours with >=3% of trades — the "active" hours. A cron bot
    # has 1-2 active hours; a retail human has 4-10 spread across open
    # and close windows; a flat 24/7 bot has ~24 active hours.
    active_hours = sum(1 for p in probs if p >= 0.03)

    # U-shape test: sum of (US-crypto morning + late-evening) hours
    # versus the mid-day lull. Humans cluster at 13-15 UTC (morning)
    # and 20-22 UTC (evening) with reduced mid-day activity.
    early_late = sum(probs[13:16]) + sum(probs[20:23])
    mid = sum(probs[4:12])
    u_shape_ratio = early_late / mid if mid > 0 else float("inf")

    if cv < 0.30 and active_hours >= 20:
        state = "medium_bot"  # flat 24/7 distribution
    elif active_hours <= 2:
        # Tight single-spike — cron-scheduled bot writing to the book
        # once or twice per day.
        state = "strong_bot"
    elif u_shape_ratio >= 2.0 and 3 <= active_hours <= 12:
        state = "medium_human"  # retail U shape with real width
    elif 3 <= active_hours <= 12 and cv >= 0.50:
        state = "weak_human"  # some shape but not clear U
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B10_intraday_emotion_shape",
        log_lr_bits=get_lr("B10_intraday_emotion_shape", state),
        value={
            "cv": round(cv, 3),
            "u_shape_ratio": round(u_shape_ratio, 3),
            "active_hours": active_hours,
            "n_trades": total,
        },
        state=state,
        detail=f"hourly CV={cv:.2f}, U-ratio={u_shape_ratio:.2f}, active={active_hours}h",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ALL_PSYCHOLOGY_SIGNALS = [
    signal_b6_disposition_effect,
    signal_b7_loss_chase_sizing,
    signal_b8_hot_hand_tempo,
    signal_b9_tilt_spike,
    signal_s8_round_pnl_exits,
    signal_s9_anchor_exits,
    signal_t9_gap_entropy,
    signal_b10_intraday_emotion_shape,
]

#: Stable set used by the classifier HUMAN_ASSISTED evaluator. Any
#: signal listed here counts as "psychology evidence" when checking how
#: many human-leaning signals have fired in an evidence log.
PSYCHOLOGY_SIGNAL_NAMES = frozenset(
    {
        "B6_disposition_effect",
        "B7_loss_chase_sizing",
        "B8_hot_hand_tempo",
        "B9_tilt_spike",
        "S8_round_pnl_exits",
        "S9_anchor_exits",
        "T9_gap_entropy",
        "B10_intraday_emotion_shape",
    }
)
