"""Phase 3 behavioral signals: price-reaction lag (B4) and pre-spike
entry rate (B4b).

These measure how quickly an agent reacts to volatility events in the
5 m candle stream fetched by backend/candle_fetcher.py. Sub-second
reaction lag requires 1 m or tick data — with 5 m buckets we can only
measure "same bucket", "one bucket later", etc. The LR ceilings reflect
that coarser resolution (max +3 bits for strong_bot, vs the +5 bits a
sub-second sampler would justify).
"""
from __future__ import annotations

import bisect
import math
from collections import defaultdict

from ..base import EvidenceScore, SignalContext
from ..calibration import get_lr

# 5 m candle window in milliseconds. Matches candle_fetcher.INTERVAL_MS.
BUCKET_MS = 5 * 60 * 1000

# A candle qualifies as a "spike" when either of these conditions holds:
#   (a) the intra-candle range is >=0.5 % of open;
#   (b) the close-to-close return from the previous candle is >=0.5 %.
SPIKE_THRESHOLD = 0.005

# Maximum number of buckets between spike and trade we still count as a
# reaction. Past three buckets (15 minutes) the causal link is noisy.
MAX_LAG_BUCKETS = 3

# Minimum number of spike-after-trade pairs before we emit evidence.
MIN_SAMPLES = 10


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _candles_by_coin(ctx: SignalContext) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for coin, candles in (ctx.candles or {}).items():
        if not candles:
            continue
        out[coin] = sorted(candles, key=lambda c: c.get("ts_ms") or 0)
    return out


def _find_spike_candles(candles: list[dict]) -> list[dict]:
    """Return the subset of candles that qualify as a price spike.

    A spike is either a >=0.5 % intra-candle range or a >=0.5 %
    close-to-close move versus the previous candle.
    """
    if not candles:
        return []
    spikes: list[dict] = []
    prev_close: float | None = None
    for c in candles:
        high = c.get("high")
        low = c.get("low")
        open_ = c.get("open")
        close = c.get("close")
        if (
            open_ is None
            or high is None
            or low is None
            or close is None
            or open_ <= 0
        ):
            prev_close = close if close is not None else prev_close
            continue
        intra_range = (high - low) / open_
        close_to_close = 0.0
        if prev_close is not None and prev_close > 0:
            close_to_close = abs(close - prev_close) / prev_close
        if intra_range >= SPIKE_THRESHOLD or close_to_close >= SPIKE_THRESHOLD:
            spikes.append(c)
        prev_close = close
    return spikes


def _collect_samples(ctx: SignalContext) -> dict[str, list[int]]:
    """Build ``{coin: [lag_buckets, ...]}`` across all trades.

    For each trade we locate the nearest spike candle within
    MAX_LAG_BUCKETS and record the lag in bucket units. Negative lag
    means the trade opened BEFORE the spike (kept for B4b).

    Spike timestamps are already sorted ascending (candles flow in
    chronological order), so we use ``bisect`` to find the nearest
    spike in O(log n) instead of scanning the whole list per trade.
    """
    by_coin = _candles_by_coin(ctx)
    spike_ts_by_coin: dict[str, list[int]] = {}
    for coin, candles in by_coin.items():
        spike_ts_by_coin[coin] = [c["ts_ms"] for c in _find_spike_candles(candles)]

    lag_samples: dict[str, list[int]] = defaultdict(list)
    for t in ctx.trades:
        coin = t.get("coin")
        opened = t.get("opened_at_ms")
        if not coin or opened is None:
            continue
        spikes = spike_ts_by_coin.get(coin)
        if not spikes:
            continue
        trade_bucket_start = (opened // BUCKET_MS) * BUCKET_MS
        # Nearest spike lookup via bisect — we only need to check the
        # spike immediately at or after trade_bucket_start and the one
        # immediately before it.
        idx = bisect.bisect_left(spikes, trade_bucket_start)
        candidates: list[int] = []
        if idx < len(spikes):
            candidates.append(spikes[idx])
        if idx > 0:
            candidates.append(spikes[idx - 1])
        best_lag: int | None = None
        for spike_ts in candidates:
            lag = (trade_bucket_start - spike_ts) // BUCKET_MS
            if abs(lag) > MAX_LAG_BUCKETS:
                continue
            if best_lag is None or abs(lag) < abs(best_lag):
                best_lag = int(lag)
        if best_lag is not None:
            lag_samples[coin].append(best_lag)
    return lag_samples


def _flatten_positive_lags(samples: dict[str, list[int]]) -> list[int]:
    """Keep only non-negative lags (trade AFTER spike), for B4."""
    out: list[int] = []
    for lags in samples.values():
        out.extend(lag for lag in lags if lag >= 0)
    return out


def _median_int(values: list[int]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _cv(values: list[int]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) / abs(mean)


# ---------------------------------------------------------------------------
# B4 price_reaction_lag
# ---------------------------------------------------------------------------

def signal_b4_price_reaction_lag(ctx: SignalContext) -> EvidenceScore | None:
    if not ctx.candles:
        return None
    samples = _collect_samples(ctx)
    post_spike_lags = _flatten_positive_lags(samples)
    if len(post_spike_lags) < MIN_SAMPLES:
        return None

    median_lag = _median_int(post_spike_lags)
    same_bucket = sum(1 for lag in post_spike_lags if lag == 0)
    same_bucket_pct = same_bucket / len(post_spike_lags)
    within_one = sum(1 for lag in post_spike_lags if lag <= 1)
    within_one_pct = within_one / len(post_spike_lags)
    lag_cv = _cv(post_spike_lags)

    if median_lag == 0 and same_bucket_pct >= 0.7:
        state = "strong_bot"
    elif median_lag == 0 or same_bucket_pct >= 0.5:
        state = "medium_bot"
    elif median_lag <= 1 and within_one_pct >= 0.6:
        state = "weak_bot"
    elif median_lag >= 2 and lag_cv > 1.0:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B4_price_reaction_lag",
        log_lr_bits=get_lr("B4_price_reaction_lag", state),
        value={
            "median_lag_buckets": median_lag,
            "same_bucket_pct": round(same_bucket_pct, 3),
            "within_one_pct": round(within_one_pct, 3),
            "lag_cv": round(lag_cv, 3),
            "n": len(post_spike_lags),
        },
        state=state,
        detail=(
            f"{len(post_spike_lags)} spike-after-trade samples; "
            f"median lag {int(median_lag)} buckets, "
            f"{same_bucket_pct * 100:.0f}% same-bucket"
        ),
    )


# ---------------------------------------------------------------------------
# B4b pre_spike_entry_rate
# ---------------------------------------------------------------------------

def signal_b4b_pre_spike_entry_rate(ctx: SignalContext) -> EvidenceScore | None:
    if not ctx.candles:
        return None
    samples = _collect_samples(ctx)
    all_lags: list[int] = []
    for lags in samples.values():
        all_lags.extend(lags)
    if len(all_lags) < MIN_SAMPLES:
        return None

    pre_spike = sum(1 for lag in all_lags if lag < 0)
    pre_spike_rate = pre_spike / len(all_lags)

    if pre_spike_rate > 0.3:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="B4b_pre_spike_entry_rate",
        log_lr_bits=get_lr("B4b_pre_spike_entry_rate", state),
        value={
            "pre_spike_rate": round(pre_spike_rate, 3),
            "n": len(all_lags),
        },
        state=state,
        detail=(
            f"{len(all_lags)} spike-proximate trades; "
            f"{pre_spike_rate * 100:.0f}% opened before the spike"
        ),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ALL_REACTION_SIGNALS = [
    signal_b4_price_reaction_lag,
    signal_b4b_pre_spike_entry_rate,
]
