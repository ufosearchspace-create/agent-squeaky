"""Unit tests for scoring_engine.signals.temporal T1..T8."""
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
from scoring_engine.signals.temporal import (  # noqa: E402
    ALL_TEMPORAL_SIGNALS,
    signal_t1_per_day_sleep_gap,
    signal_t2_sleep_window_stability,
    signal_t3_weekend_weekday_ratio,
    signal_t4_daily_volume_cv,
    signal_t5_dead_days,
    signal_t6_intraday_burst_score,
    signal_t7_per_day_interval_cv,
    signal_t8_ms_entropy,
)
from tests.fixtures_synth import (  # noqa: E402
    _to_row,
    bot_24_7,
    bot_24_7_zero_ms,
    human_daily_trader,
    scalper_bot,
)


def _ctx(trades):
    return SignalContext(agent={"id": "x"}, trades=trades, candles={}, onchain=None, now_ms=0)


# ---------------- T1 per_day_sleep_gap ----------------

def test_t1_strong_bot_for_24_7_trader():
    ev = signal_t1_per_day_sleep_gap(_ctx(bot_24_7(days=10)))
    assert ev is not None
    assert ev.state == "strong_bot"
    assert ev.log_lr_bits > 0


def test_t1_strong_human_for_consistent_sleeper():
    ev = signal_t1_per_day_sleep_gap(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.log_lr_bits < 0


def test_t1_none_when_too_few_days():
    ev = signal_t1_per_day_sleep_gap(_ctx(bot_24_7(days=3)))
    assert ev is None


# ---------------- T2 sleep_window_stability ----------------

def test_t2_human_sleeper_is_stable():
    ev = signal_t2_sleep_window_stability(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.log_lr_bits < 0


def test_t2_bot_returns_none_because_t1_not_human():
    # No sleep gap → T2 is not applicable.
    ev = signal_t2_sleep_window_stability(_ctx(bot_24_7(days=10)))
    assert ev is None


# ---------------- T3 weekend_weekday_ratio ----------------

def test_t3_human_lower_weekend_volume_is_human_signal():
    ev = signal_t3_weekend_weekday_ratio(_ctx(human_daily_trader(days=14, weekend_fraction=0.3)))
    assert ev is not None
    assert ev.state in ("medium_human", "weak_human")
    assert ev.log_lr_bits < 0


def test_t3_bot_flat_weekends():
    ev = signal_t3_weekend_weekday_ratio(_ctx(bot_24_7(days=14)))
    # 24/7 bot has ratio ≈ 1.0 → neutral, not weak_bot.
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- T4 daily_volume_cv ----------------

def test_t4_bot_uniform_volume_is_strong_bot():
    ev = signal_t4_daily_volume_cv(_ctx(bot_24_7(days=10)))
    assert ev is not None
    assert ev.state in ("medium_bot", "strong_bot")
    assert ev.log_lr_bits > 0


def test_t4_human_variable_volume_is_human():
    ev = signal_t4_daily_volume_cv(_ctx(human_daily_trader(days=14)))
    assert ev is not None
    # Human daily volume has weekend drops → cv > 0.3 usually
    assert ev.state in ("medium_human", "weak_human", "neutral")


# ---------------- T5 dead_days ----------------

def test_t5_no_dead_days_across_14_is_medium_bot():
    ev = signal_t5_dead_days(_ctx(bot_24_7(days=14)))
    assert ev is not None
    assert ev.state == "medium_bot"


def test_t5_humans_with_gap_are_human():
    # Inject a gap: skip day 5 entirely.
    trades = human_daily_trader(days=10)
    trades = [t for t in trades if not _is_in_day(t, 5)]
    ev = signal_t5_dead_days(_ctx(trades))
    assert ev is not None
    assert ev.state in ("medium_human", "weak_human")


def _is_in_day(t, day_idx):
    from datetime import datetime, timezone
    d = datetime.fromtimestamp(t["closed_at_ms"] / 1000, tz=timezone.utc).date()
    from tests.fixtures_synth import _EPOCH
    return (d - _EPOCH.date()).days == day_idx


# ---------------- T6 intraday_burst_score ----------------

def test_t6_scalper_has_high_burst():
    ev = signal_t6_intraday_burst_score(_ctx(scalper_bot(n=200)))
    assert ev is not None
    assert ev.state in ("weak_bot", "medium_bot")
    assert ev.log_lr_bits > 0


def test_t6_human_neutral():
    ev = signal_t6_intraday_burst_score(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- T7 per_day_interval_cv ----------------

def test_t7_bot_tight_intervals_are_weak_bot():
    ev = signal_t7_per_day_interval_cv(_ctx(bot_24_7(days=10, trades_per_hour=6)))
    assert ev is not None
    assert ev.state == "weak_bot"


def test_t7_human_variable_intervals_neutral():
    ev = signal_t7_per_day_interval_cv(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- T8 ms_entropy ----------------

def test_t8_zero_ms_bot_is_strong_bot():
    ev = signal_t8_ms_entropy(_ctx(bot_24_7_zero_ms(days=10)))
    assert ev is not None
    assert ev.state == "strong_bot"


def test_t8_variable_ms_is_neutral():
    ev = signal_t8_ms_entropy(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- Registration ----------------

def test_all_temporal_signals_has_all_eight():
    assert len(ALL_TEMPORAL_SIGNALS) == 8
    names = {s.__name__ for s in ALL_TEMPORAL_SIGNALS}
    assert "signal_t1_per_day_sleep_gap" in names
    assert "signal_t8_ms_entropy" in names
