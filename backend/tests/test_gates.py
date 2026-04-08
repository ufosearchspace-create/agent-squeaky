"""Unit tests for scoring_engine.gates hard override rules."""
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine.gates import (  # noqa: E402
    apply_hard_gates,
    hg2_persistent_247,
    hg3_coordinated_farm,
)
from tests.fixtures_synth import bot_24_7, human_daily_trader  # noqa: E402


# -------- HG2 --------

def test_hg2_fires_on_24_7_bot_across_10_days():
    trades = bot_24_7(days=10, trades_per_hour=4)
    assert hg2_persistent_247(trades) is True


def test_hg2_does_not_fire_for_short_window_even_if_24_7():
    trades = bot_24_7(days=5, trades_per_hour=4)
    assert hg2_persistent_247(trades) is False


def test_hg2_does_not_fire_for_human():
    trades = human_daily_trader(days=14)
    assert hg2_persistent_247(trades) is False


# -------- HG3 --------

def test_hg3_fires_on_four_identical_fingerprints():
    cluster = [
        {"id": "a", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "b", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "c", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "d", "fingerprint": (0.10, 0.8, 0.5, 3.9, 5.0)},
    ]
    assert hg3_coordinated_farm(cluster) is True


def test_hg3_does_not_fire_on_divergent_cluster():
    cluster = [
        {"id": "a", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "b", "fingerprint": (0.80, 0.1, 0.2, 0.5, 3.0)},
        {"id": "c", "fingerprint": (0.50, 0.5, 0.4, 2.0, 10.0)},
        {"id": "d", "fingerprint": (0.30, 0.7, 0.1, 1.0, 20.0)},
    ]
    assert hg3_coordinated_farm(cluster) is False


def test_hg3_requires_at_least_four_agents():
    cluster = [
        {"id": "a", "fingerprint": (0.0, 0.0, 0.0, 0.0, 0.0)},
        {"id": "b", "fingerprint": (0.0, 0.0, 0.0, 0.0, 0.0)},
        {"id": "c", "fingerprint": (0.0, 0.0, 0.0, 0.0, 0.0)},
    ]
    assert hg3_coordinated_farm(cluster) is False


# -------- apply_hard_gates integration --------

def test_label_override_wins():
    trades = human_daily_trader(days=10)
    final, hits = apply_hard_gates(
        agent={"id": "1"},
        trades=trades,
        owner_cluster=[],
        onchain=None,
        label="BOT",
        natural_class="LIKELY_HUMAN",
    )
    assert final == "BOT"
    assert hits == ["gate:labeled"]


def test_unsure_label_does_not_override():
    trades = bot_24_7(days=10)
    final, hits = apply_hard_gates(
        agent={"id": "1"},
        trades=trades,
        owner_cluster=[],
        onchain=None,
        label="UNSURE",
        natural_class="UNCERTAIN",
    )
    # UNSURE is a human-entered memo, not a hard label — do not force class.
    # But HG2 still fires because the trades are 24/7.
    assert final == "BOT"
    assert "gate:persistent_24_7" in hits


def test_hg2_forces_bot_over_natural():
    trades = bot_24_7(days=12)
    final, hits = apply_hard_gates(
        agent={"id": "1"},
        trades=trades,
        owner_cluster=[],
        onchain=None,
        label=None,
        natural_class="UNCERTAIN",  # suppose Bayesian put it here
    )
    assert final == "BOT"
    assert "gate:persistent_24_7" in hits


def test_hg3_forces_bot_on_farm():
    cluster = [
        {"id": f"sib{i}", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)} for i in range(4)
    ]
    final, hits = apply_hard_gates(
        agent={"id": "sib0"},
        trades=human_daily_trader(days=5),
        owner_cluster=cluster,
        onchain=None,
        label=None,
        natural_class="LIKELY_HUMAN",
    )
    assert final == "BOT"
    assert any(h.startswith("gate:farm_") for h in hits)


def test_no_gate_returns_natural_class():
    trades = human_daily_trader(days=10)
    final, hits = apply_hard_gates(
        agent={"id": "1"},
        trades=trades,
        owner_cluster=[],
        onchain=None,
        label=None,
        natural_class="LIKELY_HUMAN",
    )
    assert final == "LIKELY_HUMAN"
    assert hits == []
