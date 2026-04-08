"""Unit tests for scoring_engine.bayesian posterior aggregator."""
import math
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine.base import EvidenceScore  # noqa: E402
from scoring_engine.bayesian import (  # noqa: E402
    PRIOR_LOG_ODDS_BITS,
    PRIOR_P_BOT,
    posterior,
)


def _ev(name: str, bits: float, state: str = "test") -> EvidenceScore:
    return EvidenceScore(signal=name, log_lr_bits=bits, value={}, state=state, detail="")


def test_prior_matches_95_5_in_bits():
    assert PRIOR_P_BOT == 0.95
    assert abs(PRIOR_LOG_ODDS_BITS - math.log2(0.95 / 0.05)) < 1e-9


def test_no_evidence_returns_prior():
    p, log_odds, log = posterior([])
    assert abs(log_odds - PRIOR_LOG_ODDS_BITS) < 1e-9
    assert log == []
    assert p > 0.94
    assert p < 0.96


def test_single_human_evidence_shifts_down():
    p_prior, _, _ = posterior([])
    p, _, log = posterior([_ev("T1", -1.5)])
    assert p < p_prior
    assert len(log) == 1
    assert log[0]["log_lr_bits"] == -1.5


def test_strong_human_evidence_flips_to_human():
    # Prior ≈ +4.25 bits; -5 + -3 = -8 → posterior -3.75 → p ≈ 0.069.
    # That's well inside the HUMAN band (< 0.30) which is what matters.
    p, log_odds, log = posterior([
        _ev("T1", -5.0, "strong_human"),
        _ev("T2", -3.0, "strong_human"),
    ])
    assert p < 0.30
    assert log_odds < 0
    assert len(log) == 2


def test_very_strong_human_evidence_reaches_extreme_human():
    # With larger margin the posterior should saturate low.
    p, _, _ = posterior([
        _ev("T1", -5.0),
        _ev("T2", -5.0),
        _ev("B3", -3.0),
    ])
    assert p < 0.01


def test_multiple_strong_bot_evidence_saturates():
    p, log_odds, _ = posterior([_ev(f"S{i}", 3.0) for i in range(5)])
    assert log_odds > 15
    assert p > 0.99999


def test_none_entries_are_skipped():
    p, _, log = posterior([None, _ev("T1", -1.5), None])
    assert len(log) == 1
    assert log[0]["signal"] == "T1"


def test_evidence_log_preserves_value_and_detail():
    ev = EvidenceScore(
        signal="S4", log_lr_bits=1.5,
        value={"unique_coins": 15},
        state="medium_bot",
        detail="15 distinct coins across 100 trades",
    )
    _, _, log = posterior([ev])
    assert log[0]["value"] == {"unique_coins": 15}
    assert log[0]["state"] == "medium_bot"
    assert log[0]["detail"].startswith("15 distinct")
