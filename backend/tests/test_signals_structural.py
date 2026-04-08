"""Unit tests for scoring_engine.signals.structural S1..S7."""
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
from scoring_engine.signals.structural import (  # noqa: E402
    ALL_STRUCTURAL_SIGNALS,
    signal_s1_round_size_pct,
    signal_s2_size_decimal_precision,
    signal_s3_benford_compliance,
    signal_s4_coin_diversity,
    signal_s5_size_ladder_pattern,
    signal_s6_identical_size_repetition,
    signal_s7_leverage_variance,
)
from tests.fixtures_synth import (  # noqa: E402
    bot_24_7,
    human_daily_trader,
    ladder_bot,
    multi_coin_rapid_bot,
)


def _ctx(trades):
    return SignalContext(agent={"id": "x"}, trades=trades, candles={}, onchain=None, now_ms=0)


# ---------------- S1 round_size_pct ----------------

def test_s1_bot_precise_sizes_weak_bot_or_neutral():
    ev = signal_s1_round_size_pct(_ctx(bot_24_7(days=5)))
    assert ev is not None
    # bot_24_7 uses 99.2262... — not round → weak_bot
    assert ev.state in ("weak_bot", "neutral")


def test_s1_mostly_round_sizes_is_human():
    # Build a trader that uses 100, 200, 500 frequently.
    trades = []
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    for i in range(50):
        size = [100, 250, 500][i % 3] if i % 2 == 0 else 73.5 + i
        trades.append(_to_row(_EPOCH + timedelta(hours=i), size=float(size)))
    ev = signal_s1_round_size_pct(_ctx(trades))
    assert ev is not None
    assert ev.state in ("medium_human", "weak_human")
    assert ev.log_lr_bits < 0


# ---------------- S2 size_decimal_precision ----------------

def test_s2_precise_decimals_is_bot():
    ev = signal_s2_size_decimal_precision(_ctx(bot_24_7(days=5)))
    assert ev is not None
    # 99.22625500000001 → many decimals
    assert ev.state in ("medium_bot", "strong_bot")
    assert ev.log_lr_bits > 0


def test_s2_integer_sizes_is_neutral():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=float(100 + i * 10)) for i in range(30)]
    ev = signal_s2_size_decimal_precision(_ctx(trades))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- S3 benford_compliance ----------------

def test_s3_uniform_sizes_fail_benford():
    # All sizes start with digit 1 — strongly violates Benford's distribution.
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [
        _to_row(_EPOCH + timedelta(hours=i), size=100.0 + (i % 10))
        for i in range(50)
    ]
    ev = signal_s3_benford_compliance(_ctx(trades))
    assert ev is not None
    assert ev.state == "medium_bot"


def test_s3_returns_none_for_small_sample():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=100.0) for i in range(10)]
    ev = signal_s3_benford_compliance(_ctx(trades))
    assert ev is None


# ---------------- S4 coin_diversity ----------------

def test_s4_many_coins_strong_bot():
    ev = signal_s4_coin_diversity(_ctx(multi_coin_rapid_bot(days=7, trades_per_day=40)))
    assert ev is not None
    assert ev.state in ("medium_bot", "strong_bot")


def test_s4_few_coins_weak_human():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), coin="BTC" if i % 2 == 0 else "ETH", size=100 + i)
              for i in range(30)]
    ev = signal_s4_coin_diversity(_ctx(trades))
    assert ev is not None
    assert ev.state == "weak_human"


# ---------------- S5 size_ladder_pattern ----------------

def test_s5_ladder_bot_is_strong_bot():
    ev = signal_s5_size_ladder_pattern(_ctx(ladder_bot(n=60)))
    assert ev is not None
    assert ev.state == "strong_bot"


def test_s5_variable_sizes_neutral():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=float(50 + i * 3 + (i * i) % 17))
              for i in range(40)]
    ev = signal_s5_size_ladder_pattern(_ctx(trades))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- S6 identical_size_repetition ----------------

def test_s6_identical_sizes_strong_bot():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=99.22) for i in range(30)]
    ev = signal_s6_identical_size_repetition(_ctx(trades))
    assert ev is not None
    assert ev.state == "strong_bot"


def test_s6_all_unique_is_weak_human():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=100.0 + i * 1.37) for i in range(40)]
    ev = signal_s6_identical_size_repetition(_ctx(trades))
    assert ev is not None
    assert ev.state == "weak_human"


# ---------------- S7 leverage_variance ----------------

def test_s7_single_leverage_medium_bot():
    ev = signal_s7_leverage_variance(_ctx(bot_24_7(days=5, leverage=5)))
    assert ev is not None
    assert ev.state == "medium_bot"


def test_s7_many_leverages_weak_human():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    levs = [1, 3, 5, 10, 20]
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=100, leverage=levs[i % 5])
              for i in range(50)]
    ev = signal_s7_leverage_variance(_ctx(trades))
    assert ev is not None
    assert ev.state == "weak_human"


def test_s7_none_when_leverage_missing():
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = [_to_row(_EPOCH + timedelta(hours=i), size=100, leverage=5) for i in range(5)]
    for t in trades:
        t["leverage"] = None
    ev = signal_s7_leverage_variance(_ctx(trades))
    assert ev is None


# ---------------- Registration ----------------

def test_all_structural_signals_has_all_seven():
    assert len(ALL_STRUCTURAL_SIGNALS) == 7
    names = {s.__name__ for s in ALL_STRUCTURAL_SIGNALS}
    assert "signal_s1_round_size_pct" in names
    assert "signal_s7_leverage_variance" in names
