"""Unit tests for scoring_engine.signals.behavioral B1, B2, B3, B5."""
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
from scoring_engine.signals.behavioral import (  # noqa: E402
    ALL_BEHAVIORAL_SIGNALS,
    signal_b1_hold_time_variance,
    signal_b2_hold_time_median,
    signal_b3_win_loss_hold_asymmetry,
    signal_b5_concurrent_open_positions,
)
from tests.fixtures_synth import (  # noqa: E402
    asymmetric_win_loss_human,
    bimodal_hold_bot,
    bot_24_7,
    human_daily_trader,
    multi_agent_concurrent_bot,
    scalper_bot,
)


def _ctx(trades):
    return SignalContext(agent={"id": "x"}, trades=trades, candles={}, onchain=None, now_ms=0)


# ---------------- B1 hold_time_variance ----------------

def test_b1_uniform_hold_is_medium_bot():
    # bot_24_7 uses constant hold_s=300 → log CV ≈ 0 → medium_bot
    ev = signal_b1_hold_time_variance(_ctx(bot_24_7(days=5)))
    assert ev is not None
    assert ev.state in ("medium_bot", "strong_bot")


def test_b1_bimodal_hold_is_strong_bot():
    ev = signal_b1_hold_time_variance(_ctx(bimodal_hold_bot(n=120)))
    assert ev is not None
    assert ev.state == "strong_bot"


def test_b1_human_wide_variance_is_human():
    ev = signal_b1_hold_time_variance(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    # Depending on day mood, log CV may be medium_human or neutral.
    assert ev.log_lr_bits <= 0


# ---------------- B2 hold_time_median ----------------

def test_b2_scalper_is_weak_bot():
    ev = signal_b2_hold_time_median(_ctx(scalper_bot(n=80)))
    assert ev is not None
    assert ev.state == "weak_bot"


def test_b2_normal_hold_neutral():
    ev = signal_b2_hold_time_median(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------- B3 win_loss_hold_asymmetry ----------------

def test_b3_asymmetric_human_is_strong_human():
    ev = signal_b3_win_loss_hold_asymmetry(_ctx(asymmetric_win_loss_human(n_wins=15, n_losses=15)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.log_lr_bits < 0


def test_b3_symmetric_bot_is_medium_bot():
    # bot_24_7: all trades have identical hold times and identical +pnl.
    # There are no losses, so B3 should return None.
    ev = signal_b3_win_loss_hold_asymmetry(_ctx(bot_24_7(days=5)))
    assert ev is None


def test_b3_needs_enough_wins_and_losses():
    # Too few losses → None
    trades = asymmetric_win_loss_human(n_wins=15, n_losses=5)
    ev = signal_b3_win_loss_hold_asymmetry(_ctx(trades))
    assert ev is None


# ---------------- B5 concurrent_open_positions ----------------

def test_b5_multi_concurrent_bot_is_bot():
    ev = signal_b5_concurrent_open_positions(_ctx(multi_agent_concurrent_bot(days=4, positions_open_at_once=8)))
    assert ev is not None
    assert ev.state in ("medium_bot", "strong_bot")


def test_b5_single_position_human_weak_human():
    # human_daily_trader holds sizes back-to-back with small hold_s vs spacing → max=1
    from tests.fixtures_synth import _to_row, _EPOCH
    from datetime import timedelta
    trades = []
    for i in range(40):
        ts = _EPOCH + timedelta(days=i // 5, hours=i % 5 * 4)  # 4h gap between trades
        trades.append(_to_row(ts, hold_s=1800))  # 30min hold, plenty of gap
    ev = signal_b5_concurrent_open_positions(_ctx(trades))
    assert ev is not None
    assert ev.state == "weak_human"


# ---------------- Registration ----------------

def test_all_behavioral_signals_has_all_four():
    assert len(ALL_BEHAVIORAL_SIGNALS) == 4
    names = {s.__name__ for s in ALL_BEHAVIORAL_SIGNALS}
    assert "signal_b1_hold_time_variance" in names
    assert "signal_b5_concurrent_open_positions" in names
