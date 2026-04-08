"""Unit tests for scoring_engine.calibration LR loader."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine import calibration  # noqa: E402


def _fake_sb(rows):
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value.eq.return_value
    chain.execute.return_value.data = rows
    return sb


def test_reload_cache_loads_active_rows():
    rows = [
        {
            "signal_name": "T1_test",
            "version": 3,
            "thresholds": {
                "states": {
                    "strong_human": -5.0,
                    "neutral": 0.0,
                    "strong_bot": 3.0,
                }
            },
            "active": True,
        },
        {
            "signal_name": "S2_test",
            "version": 1,
            "thresholds": {"states": {"medium_bot": 1.5}},
            "active": True,
        },
    ]
    with patch("scoring_engine.calibration.get_client", return_value=_fake_sb(rows)):
        version = calibration.reload_cache()
    assert version == 3
    assert calibration.get_lr("T1_test", "strong_human") == -5.0
    assert calibration.get_lr("T1_test", "neutral") == 0.0
    assert calibration.get_lr("T1_test", "strong_bot") == 3.0
    assert calibration.get_lr("S2_test", "medium_bot") == 1.5


def test_get_lr_missing_signal_returns_zero():
    with patch("scoring_engine.calibration.get_client", return_value=_fake_sb([])):
        calibration.reload_cache()
    assert calibration.get_lr("does_not_exist", "any") == 0.0


def test_get_lr_missing_state_returns_zero():
    rows = [
        {
            "signal_name": "T1",
            "version": 1,
            "thresholds": {"states": {"neutral": 0.0}},
            "active": True,
        }
    ]
    with patch("scoring_engine.calibration.get_client", return_value=_fake_sb(rows)):
        calibration.reload_cache()
    assert calibration.get_lr("T1", "unseen_state") == 0.0


def test_current_version_reflects_latest_reload():
    rows = [
        {"signal_name": "T1", "version": 5, "thresholds": {"states": {}}, "active": True},
    ]
    with patch("scoring_engine.calibration.get_client", return_value=_fake_sb(rows)):
        calibration.reload_cache()
    assert calibration.current_version() == 5
