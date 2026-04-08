"""Unit tests for scoring_engine.classifier thresholds."""
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine.classifier import classify  # noqa: E402


def test_thresholds_match_design():
    assert classify(0.99) == "BOT"
    assert classify(0.97) == "BOT"        # boundary inclusive
    assert classify(0.96) == "LIKELY_BOT"
    assert classify(0.85) == "LIKELY_BOT"  # boundary inclusive
    assert classify(0.84) == "UNCERTAIN"
    assert classify(0.70) == "UNCERTAIN"
    assert classify(0.60) == "UNCERTAIN"  # boundary inclusive
    assert classify(0.59) == "LIKELY_HUMAN"
    assert classify(0.45) == "LIKELY_HUMAN"
    assert classify(0.30) == "LIKELY_HUMAN"  # boundary inclusive
    assert classify(0.29) == "HUMAN"
    assert classify(0.01) == "HUMAN"


def test_boundary_values_explicit():
    assert classify(0.97) == "BOT"
    assert classify(0.85) == "LIKELY_BOT"
    assert classify(0.60) == "UNCERTAIN"
    assert classify(0.30) == "LIKELY_HUMAN"
