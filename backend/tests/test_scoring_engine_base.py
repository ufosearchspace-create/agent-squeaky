"""Unit tests for scoring_engine.base core types."""
import dataclasses
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine.base import EvidenceScore, SignalContext  # noqa: E402


def test_evidence_score_is_frozen_dataclass():
    e = EvidenceScore(
        signal="T1_test",
        log_lr_bits=-3.0,
        value={"x": 1},
        state="strong_human",
        detail="test",
    )
    assert e.signal == "T1_test"
    assert e.log_lr_bits == -3.0
    assert e.state == "strong_human"
    assert dataclasses.is_dataclass(e)
    # frozen → cannot mutate
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.log_lr_bits = 0.0  # type: ignore[misc]


def test_signal_context_holds_fields():
    ctx = SignalContext(
        agent={"id": "1"},
        trades=[{"coin": "BTC"}],
        candles={},
        onchain=None,
        now_ms=1000,
    )
    assert ctx.agent["id"] == "1"
    assert ctx.trades[0]["coin"] == "BTC"
    assert ctx.owner_cluster == []  # default


def test_signal_context_accepts_owner_cluster():
    ctx = SignalContext(
        agent={"id": "1"},
        trades=[],
        candles={},
        onchain=None,
        now_ms=0,
        owner_cluster=[{"id": "sibling"}],
    )
    assert len(ctx.owner_cluster) == 1
