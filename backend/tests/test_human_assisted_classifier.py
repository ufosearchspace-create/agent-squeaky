"""Unit tests for scoring_engine.classifier.evaluate_human_assisted (PR4).

The HUMAN_ASSISTED flag is the core output of the psychology-signal
pipeline. These tests verify the precise trigger logic: posterior
threshold, trade-count floor, label-gate override, and the
>=3-signal confluence rule (CEK R6: raised from 2 to prevent
Freqtrade/Hummingbot DCA false positives).
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

from scoring_engine.classifier import (  # noqa: E402
    HUMAN_ASSISTED_BITS_THRESHOLD,
    HUMAN_ASSISTED_MIN_P_BOT,
    HUMAN_ASSISTED_MIN_SIGNALS,
    HUMAN_ASSISTED_MIN_TRADES,
    evaluate_human_assisted,
)


def _psych(signal: str, bits: float) -> dict:
    """Build an evidence_log entry for a psychology signal."""
    return {
        "signal": signal,
        "state": "medium_human" if bits < 0 else "neutral",
        "log_lr_bits": bits,
        "value": {},
        "detail": "",
    }


def _non_psych(signal: str, bits: float) -> dict:
    return {
        "signal": signal,
        "state": "neutral",
        "log_lr_bits": bits,
        "value": {},
        "detail": "",
    }


# Three psychology signals that always satisfy the bits threshold —
# convenience for tests that care about something OTHER than signal count.
_THREE_PSYCH = [
    _psych("B6_disposition_effect", -1.0),
    _psych("B9_tilt_spike", -1.2),
    _psych("S9_anchor_exits", -0.8),
]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_fires_on_three_psychology_signals():
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=_THREE_PSYCH, trade_count=100, hard_gates_hit=[]
    )
    assert ha is True
    assert len(triggered) == 3


def test_fires_on_four_psychology_signals():
    evidence = _THREE_PSYCH + [_psych("S8_round_pnl_exits", -0.6)]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.90, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is True
    assert len(triggered) == 4


# ---------------------------------------------------------------------------
# Negative path: not enough signals (CEK R6: min_signals=3)
# ---------------------------------------------------------------------------

def test_two_psychology_signals_not_enough():
    evidence = [
        _psych("B6_disposition_effect", -1.0),
        _psych("S8_round_pnl_exits", -0.8),
    ]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.90, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is False
    # Both signals are individually collected even though count < 3.
    assert len(triggered) == 2


def test_single_psychology_signal_not_enough():
    evidence = [
        _psych("B6_disposition_effect", -1.5),
    ]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is False
    assert triggered == ["B6_disposition_effect"]


def test_zero_psychology_signals_not_enough():
    evidence = [_non_psych("T1_per_day_sleep_gap", -5.0)]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is False
    assert triggered == []


def test_psychology_signals_below_bits_threshold_do_not_count():
    # bits = -0.3 does not meet the -0.5 cutoff
    evidence = [
        _psych("B6_disposition_effect", -0.3),
        _psych("S8_round_pnl_exits", -0.3),
        _psych("B9_tilt_spike", -0.3),
    ]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is False
    assert triggered == []


def test_psychology_signals_at_exact_bits_threshold_count():
    # bits == -0.5 exactly is inclusive per the <= comparison
    evidence = [
        _psych("B6_disposition_effect", -0.5),
        _psych("S8_round_pnl_exits", -0.5),
        _psych("B9_tilt_spike", -0.5),
    ]
    ha, _ = evaluate_human_assisted(
        p_bot=0.95, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is True


# ---------------------------------------------------------------------------
# Gates: posterior floor (CEK R1: lowered from 0.75 to 0.60)
# ---------------------------------------------------------------------------

def test_posterior_below_threshold_suppresses_flag():
    # p_bot = 0.50 < HUMAN_ASSISTED_MIN_P_BOT (0.60)
    ha, triggered = evaluate_human_assisted(
        p_bot=0.50, evidence_log=_THREE_PSYCH, trade_count=50, hard_gates_hit=[]
    )
    assert ha is False
    assert triggered == []


def test_posterior_at_exact_threshold_fires():
    ha, _ = evaluate_human_assisted(
        p_bot=HUMAN_ASSISTED_MIN_P_BOT,
        evidence_log=_THREE_PSYCH,
        trade_count=50,
        hard_gates_hit=[],
    )
    assert ha is True


# ---------------------------------------------------------------------------
# Gates: trade count floor
# ---------------------------------------------------------------------------

def test_trade_count_below_threshold_suppresses_flag():
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95,
        evidence_log=_THREE_PSYCH,
        trade_count=HUMAN_ASSISTED_MIN_TRADES - 1,
        hard_gates_hit=[],
    )
    assert ha is False
    assert triggered == []


def test_trade_count_at_exact_threshold_fires():
    ha, _ = evaluate_human_assisted(
        p_bot=0.95,
        evidence_log=_THREE_PSYCH,
        trade_count=HUMAN_ASSISTED_MIN_TRADES,
        hard_gates_hit=[],
    )
    assert ha is True


# ---------------------------------------------------------------------------
# Gates: hard gate override
# ---------------------------------------------------------------------------

def test_labeled_gate_overrides_flag():
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95,
        evidence_log=_THREE_PSYCH,
        trade_count=50,
        hard_gates_hit=["gate:labeled"],
    )
    assert ha is False
    assert triggered == []


def test_other_gates_coexist_with_flag():
    for gate in (
        "gate:persistent_24_7",
        "gate:farm_4",
        "gate:throwaway_farm",
        "gate:onchain_human_ceiling",
    ):
        ha, _ = evaluate_human_assisted(
            p_bot=0.95,
            evidence_log=_THREE_PSYCH,
            trade_count=50,
            hard_gates_hit=[gate],
        )
        assert ha is True, f"HA must coexist with {gate}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_evidence_log_returns_false():
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=[], trade_count=100, hard_gates_hit=[]
    )
    assert ha is False
    assert triggered == []


def test_none_hard_gates_is_treated_as_empty():
    ha, _ = evaluate_human_assisted(
        p_bot=0.95, evidence_log=_THREE_PSYCH, trade_count=50, hard_gates_hit=None
    )
    assert ha is True


def test_non_psychology_signals_with_negative_bits_do_not_count():
    evidence = [
        _non_psych("T1_per_day_sleep_gap", -5.0),
        _non_psych("S1_round_size_pct", -1.5),
        _non_psych("B3_win_loss_hold_asymmetry", -2.0),
    ]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is False
    assert triggered == []


def test_mixed_signals_only_count_psychology():
    evidence = [
        _non_psych("T1_per_day_sleep_gap", -5.0),
        _psych("B6_disposition_effect", -1.0),  # counts
        _psych("S8_round_pnl_exits", -1.0),  # counts
        _psych("B9_tilt_spike", -0.8),  # counts
        _non_psych("S1_round_size_pct", -1.0),
    ]
    ha, triggered = evaluate_human_assisted(
        p_bot=0.95, evidence_log=evidence, trade_count=50, hard_gates_hit=[]
    )
    assert ha is True
    assert set(triggered) == {"B6_disposition_effect", "S8_round_pnl_exits", "B9_tilt_spike"}


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

def test_constants_are_sensible():
    assert 0.5 < HUMAN_ASSISTED_MIN_P_BOT < 1.0
    assert HUMAN_ASSISTED_MIN_TRADES >= 20
    assert HUMAN_ASSISTED_BITS_THRESHOLD < 0
    assert HUMAN_ASSISTED_MIN_SIGNALS >= 2
