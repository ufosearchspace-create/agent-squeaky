"""Unit tests for scoring_engine.signals.onchain M1/M2/M3/M6."""
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
from scoring_engine.signals.onchain import (  # noqa: E402
    ALL_ONCHAIN_SIGNALS,
    signal_m1_owner_wallet_age,
    signal_m2_owner_multi_chain,
    signal_m3_owner_activity_score,
    signal_m6_throwaway_owner_flag,
)


def _ctx(onchain: dict | None) -> SignalContext:
    return SignalContext(
        agent={"id": "x"}, trades=[], candles={}, onchain=onchain, now_ms=0
    )


# ---------------------------------------------------------------------------
# M1 owner_wallet_age
# ---------------------------------------------------------------------------

def test_m1_none_when_no_onchain():
    assert signal_m1_owner_wallet_age(_ctx(None)) is None


def test_m1_none_for_dead_eoa_zero_txs():
    onchain = {"age_days": None, "total_tx_count": 0, "chains_active": 0}
    assert signal_m1_owner_wallet_age(_ctx(onchain)) is None


def test_m1_medium_human_for_year_plus_wallet():
    onchain = {"age_days": 700, "total_tx_count": 30}
    ev = signal_m1_owner_wallet_age(_ctx(onchain))
    assert ev is not None
    assert ev.state == "medium_human"
    assert ev.log_lr_bits < 0


def test_m1_weak_human_for_six_month_wallet():
    ev = signal_m1_owner_wallet_age(_ctx({"age_days": 200, "total_tx_count": 20}))
    assert ev is not None
    assert ev.state == "weak_human"


def test_m1_neutral_for_mid_age_wallet():
    ev = signal_m1_owner_wallet_age(_ctx({"age_days": 60, "total_tx_count": 10}))
    assert ev is not None
    assert ev.state == "neutral"
    assert ev.log_lr_bits == 0


def test_m1_weak_bot_for_new_active_wallet():
    ev = signal_m1_owner_wallet_age(_ctx({"age_days": 5, "total_tx_count": 8}))
    assert ev is not None
    assert ev.state == "weak_bot"
    assert ev.log_lr_bits > 0


# ---------------------------------------------------------------------------
# M2 owner_multi_chain
# ---------------------------------------------------------------------------

def test_m2_none_for_dead_eoa():
    assert signal_m2_owner_multi_chain(_ctx({"chains_active": 0, "total_tx_count": 0})) is None


def test_m2_medium_human_for_five_chains():
    ev = signal_m2_owner_multi_chain(_ctx({"chains_active": 9, "total_tx_count": 100}))
    assert ev is not None
    assert ev.state == "medium_human"


def test_m2_weak_human_for_three_chains():
    ev = signal_m2_owner_multi_chain(_ctx({"chains_active": 3, "total_tx_count": 50}))
    assert ev is not None
    assert ev.state == "weak_human"


def test_m2_neutral_for_single_chain():
    ev = signal_m2_owner_multi_chain(_ctx({"chains_active": 1, "total_tx_count": 20}))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------------------------------------------------------------------
# M3 owner_activity_score
# ---------------------------------------------------------------------------

def test_m3_none_for_zero_txs():
    assert signal_m3_owner_activity_score(_ctx({"total_tx_count": 0})) is None


def test_m3_medium_human_for_hundreds_of_txs():
    ev = signal_m3_owner_activity_score(_ctx({"total_tx_count": 500}))
    assert ev is not None
    assert ev.state == "medium_human"


def test_m3_weak_human_for_dozens_of_txs():
    ev = signal_m3_owner_activity_score(_ctx({"total_tx_count": 50}))
    assert ev is not None
    assert ev.state == "weak_human"


def test_m3_neutral_for_handful_of_txs():
    ev = signal_m3_owner_activity_score(_ctx({"total_tx_count": 5}))
    assert ev is not None
    assert ev.state == "neutral"


# ---------------------------------------------------------------------------
# M6 throwaway_owner_flag
# ---------------------------------------------------------------------------

def test_m6_none_when_no_onchain():
    assert signal_m6_throwaway_owner_flag(_ctx(None)) is None


def test_m6_neutral_for_dead_eoa_explicit_entry():
    """Dead EOA must return an explicit neutral evidence, not None —
    we want the evidence_log to make it clear this pattern is NOT
    throwaway even though both have 0 active days of signal.
    """
    ev = signal_m6_throwaway_owner_flag(_ctx({"age_days": None, "total_tx_count": 0}))
    assert ev is not None
    assert ev.state == "neutral"
    assert ev.log_lr_bits == 0


def test_m6_medium_bot_for_young_active_throwaway():
    ev = signal_m6_throwaway_owner_flag(_ctx({"age_days": 7, "total_tx_count": 5}))
    assert ev is not None
    assert ev.state == "medium_bot"
    assert ev.log_lr_bits > 0


def test_m6_weak_bot_for_almost_month_old_low_activity():
    ev = signal_m6_throwaway_owner_flag(_ctx({"age_days": 25, "total_tx_count": 15}))
    assert ev is not None
    assert ev.state == "weak_bot"


def test_m6_neutral_for_established_wallet():
    ev = signal_m6_throwaway_owner_flag(_ctx({"age_days": 400, "total_tx_count": 150}))
    assert ev is not None
    assert ev.state == "neutral"


def test_m6_none_when_age_unknown_but_has_txs():
    # Edge case: onchain exists, txs > 0, but age_days missing (parser
    # failed to read First: block). Prefer None over guessing.
    ev = signal_m6_throwaway_owner_flag(_ctx({"age_days": None, "total_tx_count": 20}))
    assert ev is None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_all_onchain_signals_has_four():
    assert len(ALL_ONCHAIN_SIGNALS) == 4
    names = {s.__name__ for s in ALL_ONCHAIN_SIGNALS}
    assert names == {
        "signal_m1_owner_wallet_age",
        "signal_m2_owner_multi_chain",
        "signal_m3_owner_activity_score",
        "signal_m6_throwaway_owner_flag",
    }
