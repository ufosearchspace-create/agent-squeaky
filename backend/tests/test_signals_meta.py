"""Unit tests for scoring_engine.signals.meta M5 cross-agent consistency."""
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
from scoring_engine.signals.meta import (  # noqa: E402
    ALL_META_SIGNALS,
    signal_m5_cross_agent_consistency,
)


def _ctx(trades, owner_cluster):
    return SignalContext(
        agent={"id": "target"},
        trades=trades,
        candles={},
        onchain=None,
        now_ms=0,
        owner_cluster=owner_cluster,
    )


def test_m5_returns_none_for_solo_owner():
    ev = signal_m5_cross_agent_consistency(_ctx([], owner_cluster=[{"id": "target", "fingerprint": (0, 0, 0, 0, 5)}]))
    assert ev is None


def test_m5_returns_none_when_no_fingerprints():
    cluster = [
        {"id": "target"},
        {"id": "sib"},
    ]
    ev = signal_m5_cross_agent_consistency(_ctx([], owner_cluster=cluster))
    assert ev is None


def test_m5_divergent_agent_is_weak_human():
    cluster = [
        {"id": "target", "fingerprint": (0.8, 0.1, 0.2, 0.5, 3.0)},  # divergent
        {"id": "sib1", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "sib2", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "sib3", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
    ]
    ev = signal_m5_cross_agent_consistency(_ctx([], owner_cluster=cluster))
    assert ev is not None
    assert ev.state == "weak_human"
    assert ev.log_lr_bits < 0


def test_m5_homogeneous_cluster_is_neutral():
    cluster = [
        {"id": "target", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "sib1", "fingerprint": (0.05, 0.9, 0.6, 4.2, 5.0)},
        {"id": "sib2", "fingerprint": (0.06, 0.88, 0.62, 4.1, 5.0)},
    ]
    ev = signal_m5_cross_agent_consistency(_ctx([], owner_cluster=cluster))
    assert ev is not None
    assert ev.state == "neutral"


def test_all_meta_signals_registration():
    assert len(ALL_META_SIGNALS) == 1
    assert signal_m5_cross_agent_consistency in ALL_META_SIGNALS
