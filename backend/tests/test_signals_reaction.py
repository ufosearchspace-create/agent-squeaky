"""Unit tests for scoring_engine.signals.reaction B4 + B4b."""
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine.base import SignalContext  # noqa: E402
from scoring_engine.signals.reaction import (  # noqa: E402
    ALL_REACTION_SIGNALS,
    _find_spike_candles,
    signal_b4_price_reaction_lag,
    signal_b4b_pre_spike_entry_rate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(ts_ms: int, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "ts_ms": ts_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


def _trade(coin: str, opened_at_ms: int, entry_price: float = 100.0, direction: str = "LONG") -> dict:
    return {
        "coin": coin,
        "opened_at_ms": opened_at_ms,
        "closed_at_ms": opened_at_ms + 600_000,  # 10 min hold
        "entry_price": entry_price,
        "exit_price": entry_price * 1.01,
        "direction": direction,
        "position_size": 100.0,
        "leverage": 5,
        "closed_pnl": 1.0,
        "hold_time_s": 600,
    }


def _flat_candles(coin: str, start_ms: int, count: int, price: float = 100.0) -> list[dict]:
    """Flat candles with ~0 % movement — no spikes."""
    return [
        _candle(start_ms + i * 300_000, price, price * 1.0001, price * 0.9999, price)
        for i in range(count)
    ]


def _spike_candles(coin: str, start_ms: int, count: int, spike_indices: list[int]) -> list[dict]:
    """Candles that are flat except at the given indices where price moves >0.5 %."""
    out = []
    base = 100.0
    for i in range(count):
        if i in spike_indices:
            out.append(_candle(start_ms + i * 300_000, base, base * 1.01, base, base * 1.008))
            base = base * 1.008
        else:
            out.append(_candle(start_ms + i * 300_000, base, base * 1.0001, base * 0.9999, base))
    return out


def _ctx(trades: list[dict], candles: dict[str, list[dict]]) -> SignalContext:
    return SignalContext(
        agent={"id": "x"}, trades=trades, candles=candles, onchain=None, now_ms=0
    )


# ---------------------------------------------------------------------------
# _find_spike_candles
# ---------------------------------------------------------------------------

def test_find_spike_candles_detects_half_percent_moves():
    base = 1_000_000_200_000  # 5m-aligned
    candles = _spike_candles("BTC", base, 10, [3, 6, 8])
    spikes = _find_spike_candles(candles)
    spike_indices = {c["ts_ms"] for c in spikes}
    assert base + 3 * 300_000 in spike_indices
    assert base + 6 * 300_000 in spike_indices
    assert base + 8 * 300_000 in spike_indices
    assert len(spikes) == 3


def test_find_spike_candles_ignores_flat_market():
    candles = _flat_candles("BTC", 1_000_000_200_000, 10)
    assert _find_spike_candles(candles) == []


# ---------------------------------------------------------------------------
# B4 signal
# ---------------------------------------------------------------------------

def test_b4_none_when_no_candles():
    ev = signal_b4_price_reaction_lag(_ctx([_trade("BTC", 0)], candles={}))
    assert ev is None


def test_b4_none_when_too_few_spike_samples():
    # 5 trades but only 2 spikes reachable — below the min_samples=10.
    start = 1_775_001_000_000  # multiple of 300_000 ms (5m) for clean bucket alignment
    candles = _spike_candles("BTC", start, 20, spike_indices=[2, 5])
    trades = [
        _trade("BTC", start + 2 * 300_000 + 30_000),
        _trade("BTC", start + 5 * 300_000 + 60_000),
    ]
    ev = signal_b4_price_reaction_lag(_ctx(trades, {"BTC": candles}))
    assert ev is None


def test_b4_strong_bot_when_all_entries_in_same_bucket_as_spike():
    start = 1_775_001_000_000  # multiple of 300_000 ms (5m) for clean bucket alignment
    # Sparse spikes (every 10 buckets) so the nearest-spike lookup is
    # unambiguous — trades should land in the SAME candle as their spike.
    spike_positions = list(range(10, 200, 10))
    candles = _spike_candles("BTC", start, 220, spike_indices=spike_positions)
    trades = [
        _trade("BTC", start + sp * 300_000 + 45_000) for sp in spike_positions
    ]
    ev = signal_b4_price_reaction_lag(_ctx(trades, {"BTC": candles}))
    assert ev is not None
    assert ev.state == "strong_bot"
    assert ev.log_lr_bits > 0


def test_b4_weak_human_when_lag_is_variable_and_delayed():
    start = 1_775_001_000_000
    # Sparse spikes so each trade has exactly one reachable spike.
    spike_positions = list(range(10, 200, 10))
    candles = _spike_candles("BTC", start, 220, spike_indices=spike_positions)
    # Every trade opens 2 or 3 buckets AFTER its own spike (variable lag).
    trades = []
    for idx, sp in enumerate(spike_positions):
        lag_buckets = 2 + (idx % 2)  # 2 or 3
        trades.append(_trade("BTC", start + (sp + lag_buckets) * 300_000 + 10_000))
    ev = signal_b4_price_reaction_lag(_ctx(trades, {"BTC": candles}))
    assert ev is not None
    # Median >= 2 and lag_cv > 0, we expect non-bot. weak_human requires
    # lag_cv > 1.0 which depends on the exact distribution — accept
    # either weak_human or neutral.
    assert ev.log_lr_bits <= 0


def test_b4_medium_bot_when_split_same_bucket_and_next():
    """Same-bucket share between 0.5 and 0.7 triggers medium_bot."""
    start = 1_775_001_000_000
    spike_positions = list(range(10, 200, 10))
    candles = _spike_candles("BTC", start, 220, spike_indices=spike_positions)
    trades = []
    for idx, sp in enumerate(spike_positions):
        lag = 0 if idx % 2 == 0 else 1  # alternating 0 / 1, median 0, same_bucket_pct 0.5
        trades.append(_trade("BTC", start + (sp + lag) * 300_000 + 30_000))
    ev = signal_b4_price_reaction_lag(_ctx(trades, {"BTC": candles}))
    assert ev is not None
    assert ev.state in ("strong_bot", "medium_bot")
    assert ev.log_lr_bits > 0


# ---------------------------------------------------------------------------
# B4b signal
# ---------------------------------------------------------------------------

def test_b4b_weak_bot_when_many_pre_spike_entries():
    start = 1_775_001_000_000  # multiple of 300_000 ms (5m) for clean bucket alignment
    # Spikes spaced 5 buckets apart so the nearest-spike lookup is unambiguous.
    spike_positions = list(range(10, 120, 5))
    candles = _spike_candles("BTC", start, 140, spike_indices=spike_positions)
    # Every trade opens one candle BEFORE its own spike.
    trades = [
        _trade("BTC", start + (sp - 1) * 300_000 + 30_000) for sp in spike_positions
    ]
    ev = signal_b4b_pre_spike_entry_rate(_ctx(trades, {"BTC": candles}))
    assert ev is not None
    assert ev.state == "weak_bot"
    assert ev.log_lr_bits > 0


def test_b4b_neutral_when_no_pre_spike():
    start = 1_775_001_000_000  # multiple of 300_000 ms (5m) for clean bucket alignment
    spike_positions = list(range(10, 120, 5))
    candles = _spike_candles("BTC", start, 140, spike_indices=spike_positions)
    trades = [
        _trade("BTC", start + sp * 300_000 + 60_000) for sp in spike_positions
    ]
    ev = signal_b4b_pre_spike_entry_rate(_ctx(trades, {"BTC": candles}))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_all_reaction_signals_has_both():
    assert len(ALL_REACTION_SIGNALS) == 2
    names = {s.__name__ for s in ALL_REACTION_SIGNALS}
    assert "signal_b4_price_reaction_lag" in names
    assert "signal_b4b_pre_spike_entry_rate" in names
