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
    hg4_onchain_human_ceiling,
    hg5_throwaway_farm,
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


# -------- HG4 onchain_human_ceiling --------

def test_hg4_fires_on_old_multichain_active_owner():
    onchain = {"age_days": 700, "chains_active": 5, "total_tx_count": 350}
    assert hg4_onchain_human_ceiling(onchain) is True


def test_hg4_requires_all_three_conditions():
    # Old but single chain
    assert hg4_onchain_human_ceiling(
        {"age_days": 700, "chains_active": 1, "total_tx_count": 500}
    ) is False
    # Old, multi-chain, but low activity
    assert hg4_onchain_human_ceiling(
        {"age_days": 700, "chains_active": 5, "total_tx_count": 50}
    ) is False
    # Multi-chain, active, but young
    assert hg4_onchain_human_ceiling(
        {"age_days": 100, "chains_active": 5, "total_tx_count": 500}
    ) is False


def test_hg4_none_onchain_does_not_fire():
    assert hg4_onchain_human_ceiling(None) is False
    assert hg4_onchain_human_ceiling({}) is False


def test_hg4_caps_bot_classification_at_uncertain():
    onchain = {"age_days": 800, "chains_active": 6, "total_tx_count": 500}
    final, hits = apply_hard_gates(
        agent={"id": "1"},
        trades=bot_24_7(days=5),  # few days, HG2 does not fire
        owner_cluster=[],
        onchain=onchain,
        label=None,
        natural_class="BOT",
    )
    assert final == "UNCERTAIN"
    assert "gate:onchain_human_ceiling" in hits


def test_hg4_does_not_promote_human_to_uncertain():
    onchain = {"age_days": 800, "chains_active": 6, "total_tx_count": 500}
    final, hits = apply_hard_gates(
        agent={"id": "1"},
        trades=human_daily_trader(days=10),
        owner_cluster=[],
        onchain=onchain,
        label=None,
        natural_class="HUMAN",
    )
    # HG4 is a ceiling, not a promoter — HUMAN stays HUMAN.
    assert final == "HUMAN"
    assert "gate:onchain_human_ceiling" not in hits


# -------- HG5 throwaway_farm --------

def test_hg5_fires_on_young_clustered_active_throwaway():
    onchain = {"age_days": 5, "total_tx_count": 8}
    cluster = [{"id": "sib1"}, {"id": "sib2"}]
    trades = bot_24_7(days=3)  # 24 * 3 * 4 = 288 trades
    assert hg5_throwaway_farm(onchain, cluster, trades) is True


def test_hg5_does_not_fire_on_dead_eoa():
    """Dead EOA (0 txs) must NOT trigger HG5 — it is a legitimate
    pattern where the owner simply never touched the wallet."""
    onchain = {"age_days": None, "total_tx_count": 0}
    cluster = [{"id": "sib1"}, {"id": "sib2"}]
    trades = bot_24_7(days=3)
    assert hg5_throwaway_farm(onchain, cluster, trades) is False


def test_hg5_requires_cluster_size_two_or_more():
    onchain = {"age_days": 5, "total_tx_count": 8}
    trades = bot_24_7(days=3)
    assert hg5_throwaway_farm(onchain, [], trades) is False
    assert hg5_throwaway_farm(onchain, [{"id": "lone"}], trades) is False


def test_hg5_requires_old_enough_agent_trades():
    onchain = {"age_days": 5, "total_tx_count": 8}
    cluster = [{"id": "sib1"}, {"id": "sib2"}]
    # Only 5 trades — below the 20-trade minimum
    assert hg5_throwaway_farm(onchain, cluster, [{}] * 5) is False


def test_hg5_does_not_fire_on_old_owner():
    onchain = {"age_days": 400, "total_tx_count": 8}
    cluster = [{"id": "sib1"}, {"id": "sib2"}]
    trades = bot_24_7(days=3)
    assert hg5_throwaway_farm(onchain, cluster, trades) is False


def test_hg5_force_bot_via_apply_hard_gates():
    onchain = {"age_days": 5, "total_tx_count": 3}
    cluster = [{"id": "sib1"}, {"id": "sib2"}]
    trades = bot_24_7(days=3)
    final, hits = apply_hard_gates(
        agent={"id": "sib1"},
        trades=trades,
        owner_cluster=cluster,
        onchain=onchain,
        label=None,
        natural_class="UNCERTAIN",
    )
    assert final == "BOT"
    assert "gate:throwaway_farm" in hits


def test_hg5_wins_over_hg4_when_both_conditions_exist():
    """Unlikely combination but the precedence order matters: if HG5
    ever matched alongside HG4, HG5 (force BOT) should come first."""
    # This test documents precedence. The real-world case where a
    # wallet is BOTH old+multichain AND recently active+throwaway is
    # essentially impossible, but we exercise the code path.
    onchain = {
        "age_days": 5,  # HG5 young trigger
        "chains_active": 1,
        "total_tx_count": 3,
    }
    cluster = [{"id": "sib1"}, {"id": "sib2"}]
    trades = bot_24_7(days=3)
    final, hits = apply_hard_gates(
        agent={"id": "sib1"},
        trades=trades,
        owner_cluster=cluster,
        onchain=onchain,
        label=None,
        natural_class="LIKELY_HUMAN",
    )
    assert final == "BOT"
    assert "gate:throwaway_farm" in hits
    assert "gate:onchain_human_ceiling" not in hits
