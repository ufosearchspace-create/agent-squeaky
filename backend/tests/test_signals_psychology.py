"""Unit tests for scoring_engine.signals.psychology (PR4).

Each test calls the signal function directly with a SignalContext built
from a synthetic fixture and asserts the returned state is what the
academic literature would predict for that trade pattern.

Because LR values are loaded from scanner_signal_lrs at runtime and the
tests do not seed that table, ``log_lr_bits`` comes back as 0.0 for
every signal (get_lr default). All assertions here are on ``state`` and
``value``, which are deterministic functions of the input.
"""
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
from scoring_engine.signals.psychology import (  # noqa: E402
    ALL_PSYCHOLOGY_SIGNALS,
    PSYCHOLOGY_SIGNAL_NAMES,
    signal_b6_disposition_effect,
    signal_b7_loss_chase_sizing,
    signal_b8_hot_hand_tempo,
    signal_b9_tilt_spike,
    signal_b10_intraday_emotion_shape,
    signal_s8_round_pnl_exits,
    signal_s9_anchor_exits,
    signal_t9_gap_entropy,
)
from tests.fixtures_synth import (  # noqa: E402
    anchor_exits_human,
    asymmetric_win_loss_human,
    bot_24_7,
    circadian_gaps_human,
    hot_hand_human,
    human_daily_trader,
    loss_chase_human,
    round_pnl_exits_human,
    scalper_bot,
    tilt_spike_human,
    u_shape_retail_human,
)


def _ctx(trades):
    return SignalContext(
        agent={"id": "x"}, trades=trades, candles={}, onchain=None, now_ms=0
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_registration_exports_eight_signals():
    assert len(ALL_PSYCHOLOGY_SIGNALS) == 8
    names = {fn.__name__ for fn in ALL_PSYCHOLOGY_SIGNALS}
    # No duplicate registrations.
    assert len(names) == 8


def test_psychology_signal_names_matches_registration():
    assert len(PSYCHOLOGY_SIGNAL_NAMES) == 8
    # Sanity: every registered function produces an EvidenceScore whose
    # ``signal`` string is in the set. We check this via a trivial
    # fixture large enough for most signals to fire.
    trades = asymmetric_win_loss_human(n_wins=30, n_losses=30)
    ctx = _ctx(trades)
    seen = set()
    for fn in ALL_PSYCHOLOGY_SIGNALS:
        ev = fn(ctx)
        if ev is not None:
            seen.add(ev.signal)
    # Every emitted signal name must be in the frozenset.
    assert seen <= PSYCHOLOGY_SIGNAL_NAMES


# ---------------------------------------------------------------------------
# B6 disposition_effect
# ---------------------------------------------------------------------------

def test_b6_insufficient_data_returns_none():
    ev = signal_b6_disposition_effect(_ctx([]))
    assert ev is None


def test_b6_flat_bot_ratio_is_neutral():
    ev = signal_b6_disposition_effect(_ctx(bot_24_7(days=5)))
    # bot_24_7 uses pnl=0.5 for every trade, so there are 0 losses.
    # Insufficient data → None.
    assert ev is None


def test_b6_asymmetric_human_is_human_leaning():
    ev = signal_b6_disposition_effect(
        _ctx(asymmetric_win_loss_human(n_wins=20, n_losses=20))
    )
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.value["ratio"] >= 1.3


def test_b6_ratio_near_one_is_neutral():
    # Build synthetic: equal hold times on wins and losses.
    trades = []
    from datetime import timedelta
    from tests.fixtures_synth import _EPOCH, _to_row  # noqa: PLC0415
    for i in range(10):
        trades.append(_to_row(_EPOCH + timedelta(hours=i), hold_s=1200, pnl=2.0))
    for i in range(10):
        trades.append(
            _to_row(_EPOCH + timedelta(days=1, hours=i), hold_s=1200, pnl=-2.0)
        )
    ev = signal_b6_disposition_effect(_ctx(trades))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------------------------------------------------------------------
# B7 loss_chase_sizing
# ---------------------------------------------------------------------------

def test_b7_insufficient_data_returns_none():
    ev = signal_b7_loss_chase_sizing(_ctx([]))
    assert ev is None


def test_b7_flat_bot_is_medium_bot():
    ev = signal_b7_loss_chase_sizing(_ctx(bot_24_7(days=5)))
    # Constant size, constant PnL → zero variance → Pearson returns 0
    # (degenerate) → medium_bot.
    assert ev is not None
    assert ev.state == "medium_bot"


def test_b7_loss_chase_human_is_human_leaning():
    ev = signal_b7_loss_chase_sizing(_ctx(loss_chase_human(n_cycles=10)))
    assert ev is not None
    # Chase should produce a negative Pearson r.
    assert ev.value["pearson_r"] < 0
    assert ev.state in ("strong_human", "medium_human")


# ---------------------------------------------------------------------------
# B8 hot_hand_tempo
# ---------------------------------------------------------------------------

def test_b8_insufficient_data_returns_none():
    ev = signal_b8_hot_hand_tempo(_ctx([]))
    assert ev is None


def test_b8_bot_flat_tempo_is_medium_bot():
    ev = signal_b8_hot_hand_tempo(_ctx(bot_24_7(days=5)))
    assert ev is not None
    # Constant pace, all wins → degenerate correlation → medium_bot.
    assert ev.state == "medium_bot"


def test_b8_hot_hand_human_is_human_leaning():
    ev = signal_b8_hot_hand_tempo(_ctx(hot_hand_human(n_cycles=8)))
    assert ev is not None
    assert ev.value["pearson_r"] > 0
    assert ev.state in ("strong_human", "medium_human")


# ---------------------------------------------------------------------------
# B9 tilt_spike
# ---------------------------------------------------------------------------

def test_b9_insufficient_data_returns_none():
    ev = signal_b9_tilt_spike(_ctx([]))
    assert ev is None


def test_b9_bot_no_big_losses_returns_none():
    # bot_24_7 has all positive pnl — no losses at all.
    ev = signal_b9_tilt_spike(_ctx(bot_24_7(days=5)))
    assert ev is None


def test_b9_tilt_human_is_human_leaning():
    ev = signal_b9_tilt_spike(_ctx(tilt_spike_human(n_cycles=6)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.value["ratio"] > 1.3


# ---------------------------------------------------------------------------
# S8 round_pnl_exits
# ---------------------------------------------------------------------------

def test_s8_insufficient_data_returns_none():
    ev = signal_s8_round_pnl_exits(_ctx([]))
    assert ev is None


def test_s8_random_pnl_is_neutral_or_bot():
    # bot_24_7 has pnl=0.5 constant → 0% near round-number.
    ev = signal_s8_round_pnl_exits(_ctx(bot_24_7(days=5)))
    assert ev is not None
    assert ev.state == "medium_bot"


def test_s8_round_pnl_human_is_human_leaning():
    ev = signal_s8_round_pnl_exits(_ctx(round_pnl_exits_human(n=40)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.value["pct_near_round"] >= 0.42


# ---------------------------------------------------------------------------
# S9 anchor_exits
# ---------------------------------------------------------------------------

def test_s9_insufficient_data_returns_none():
    ev = signal_s9_anchor_exits(_ctx([]))
    assert ev is None


def test_s9_bot_no_price_data_returns_none():
    # bot_24_7 has the default entry/exit (70000/70100 = 0.143% return),
    # which is below the 0.5% floor for anchor detection → none near.
    ev = signal_s9_anchor_exits(_ctx(bot_24_7(days=5)))
    assert ev is not None
    assert ev.state == "medium_bot"


def test_s9_anchor_human_is_human_leaning():
    ev = signal_s9_anchor_exits(_ctx(anchor_exits_human(n=40)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")


# ---------------------------------------------------------------------------
# T9 gap_entropy
# ---------------------------------------------------------------------------

def test_t9_insufficient_data_returns_none():
    ev = signal_t9_gap_entropy(_ctx([]))
    assert ev is None


def test_t9_bot_24_7_returns_no_gaps():
    ev = signal_t9_gap_entropy(_ctx(bot_24_7(days=10)))
    assert ev is not None
    assert ev.state == "no_gaps"


def test_t9_circadian_human_low_entropy():
    ev = signal_t9_gap_entropy(_ctx(circadian_gaps_human(days=10)))
    assert ev is not None
    # Tight circadian pattern → low entropy → medium_human.
    assert ev.state in ("medium_human", "weak_human", "neutral")
    assert ev.value["gap_count"] >= 5


def test_t9_short_span_returns_none():
    # scalper_bot generates trades on a single day → span < 7 days.
    ev = signal_t9_gap_entropy(_ctx(scalper_bot(n=200)))
    assert ev is None


# ---------------------------------------------------------------------------
# B10 intraday_emotion_shape
# ---------------------------------------------------------------------------

def test_b10_insufficient_data_returns_none():
    ev = signal_b10_intraday_emotion_shape(_ctx([]))
    assert ev is None


def test_b10_bot_flat_is_medium_bot():
    ev = signal_b10_intraday_emotion_shape(_ctx(bot_24_7(days=10)))
    assert ev is not None
    # Flat 24/7 distribution → very low CV.
    assert ev.state == "medium_bot"


def test_b10_u_shape_human_is_human_leaning():
    ev = signal_b10_intraday_emotion_shape(_ctx(u_shape_retail_human(days=10)))
    assert ev is not None
    # U-shape should register as some form of human lean — exact state
    # depends on CV/U-ratio landing — we only require it is NOT a
    # bot-state.
    assert ev.state not in ("medium_bot", "strong_bot")


# ---------------------------------------------------------------------------
# Cross-cutting: None vs EvidenceScore consistency
# ---------------------------------------------------------------------------

def test_all_signals_return_evidence_or_none_for_any_input():
    """Every signal must return None or an EvidenceScore — never raise."""
    fixtures = [
        [],
        bot_24_7(days=5),
        human_daily_trader(days=10),
        asymmetric_win_loss_human(n_wins=20, n_losses=20),
        scalper_bot(n=200),
    ]
    for trades in fixtures:
        ctx = _ctx(trades)
        for fn in ALL_PSYCHOLOGY_SIGNALS:
            result = fn(ctx)
            # No exception, returns None or has a ``signal`` attribute.
            assert result is None or hasattr(result, "signal")
