"""Shared pytest fixtures for backend tests.

Pre-seeds the scoring_engine calibration LR cache with the exact values
from migration 006_seed_signal_lrs_v1.sql so signal modules can run
without a live Supabase connection.
"""
import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from scoring_engine import calibration  # noqa: E402


# Mirror of migrations/006_seed_signal_lrs_v1.sql — keep in sync when the
# live seed changes. Only the ``states`` map is needed for runtime lookups.
_V1_STATES: dict[str, dict[str, float]] = {
    # Temporal
    "T1_per_day_sleep_gap": {
        "strong_human": -5.0, "medium_human": -1.5, "neutral": 0.0,
        "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "T2_sleep_window_stability": {
        "strong_human": -3.0, "medium_human": -1.5, "neutral": 0.0,
    },
    "T3_weekend_weekday_ratio": {
        "medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5,
    },
    "T4_daily_volume_cv": {
        "medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0,
        "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "T5_dead_days": {
        "medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5,
    },
    "T6_intraday_burst_score": {
        "neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5,
    },
    "T7_per_day_interval_cv": {
        "neutral": 0.0, "weak_bot": 0.5,
    },
    "T8_ms_entropy": {
        "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
    # Structural
    "S1_round_size_pct": {
        "medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5,
    },
    "S2_size_decimal_precision": {
        "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "S3_benford_compliance": {
        "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5,
    },
    "S4_coin_diversity": {
        "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "S5_size_ladder_pattern": {
        "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "S6_identical_size_repetition": {
        "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "S7_leverage_variance": {
        "weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5,
    },
    # Meta
    "M5_cross_agent_consistency": {
        "neutral": 0.0, "weak_human": -0.5,
    },
    # Behavioral
    "B1_hold_time_variance": {
        "medium_human": -1.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
    "B2_hold_time_median": {
        "neutral": 0.0, "weak_bot": 0.5,
    },
    "B3_win_loss_hold_asymmetry": {
        "strong_human": -3.0, "medium_human": -1.5, "neutral": 0.0,
        "weak_bot": 0.5, "medium_bot": 1.5,
    },
    "B5_concurrent_open_positions": {
        "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0,
    },
}


@pytest.fixture(autouse=True)
def _seed_calibration_cache():
    """Populate the LR cache before every test and clear after."""
    calibration._CACHE = {k: dict(v) for k, v in _V1_STATES.items()}
    calibration._VERSION = 1
    try:
        yield
    finally:
        calibration._reset_for_tests()
