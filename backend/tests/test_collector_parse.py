"""Unit tests for collector's DegenClaw trade row construction."""
import json
import os
import sys
from pathlib import Path

# Make the backend package importable when running pytest from repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# config.py requires env vars at import time; inject dummies for unit tests.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from collector import _iso_to_ms, _trade_to_row  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "dgc_trade_sample.json"


def test_iso_to_ms_converts_known_timestamp():
    # 2026-04-08T14:36:34.897Z → 1775658994897
    assert _iso_to_ms("2026-04-08T14:36:34.897Z") == 1775658994897


def test_iso_to_ms_handles_none_and_empty():
    assert _iso_to_ms(None) is None
    assert _iso_to_ms("") is None


def test_iso_to_ms_returns_none_on_garbage():
    assert _iso_to_ms("not-a-date") is None


def test_trade_to_row_parses_all_new_fields():
    api = json.loads(FIXTURE.read_text())["data"][0]
    row = _trade_to_row(agent_id="137", api_trade=api)

    assert row["agent_id"] == "137"
    assert row["dgc_trade_id"] == "32987"
    assert row["opened_at_ms"] == 1775652959124
    assert row["closed_at_ms"] == 1775658994897
    assert row["closed_at_ms"] > row["opened_at_ms"]
    assert row["coin"] == "VIRTUAL"
    assert row["direction"] == "SHORT"
    assert row["entry_price"] == 0.68577
    assert row["exit_price"] == 0.67358
    assert row["position_size"] == 99.22625500000001
    assert row["leverage"] == 5
    assert row["closed_pnl"] == 0.917907


def test_trade_to_row_handles_missing_opened_at():
    api = {
        "id": "1",
        "executedAt": "2026-04-08T14:00:00Z",
        "token": "BTC",
        "direction": "LONG",
        "positionSize": 100.0,
        "leverage": 3,
        "entryPrice": 70000,
        "exitPrice": 71000,
        "realizedPnl": 10.0,
        "openedAt": None,
    }
    row = _trade_to_row(agent_id="X", api_trade=api)
    assert row["opened_at_ms"] is None
    assert row["closed_at_ms"] is not None
    assert row["dgc_trade_id"] == "1"


def test_trade_to_row_handles_missing_numeric_fields():
    api = {
        "id": "2",
        "executedAt": "2026-04-08T14:00:00Z",
        "openedAt": "2026-04-08T13:00:00Z",
        "token": "ETH",
        "direction": "LONG",
        "positionSize": None,
        "leverage": None,
        "entryPrice": None,
        "exitPrice": None,
        "realizedPnl": None,
    }
    row = _trade_to_row(agent_id="Y", api_trade=api)
    assert row["position_size"] is None
    assert row["leverage"] is None
    assert row["entry_price"] is None
    assert row["exit_price"] is None
    assert row["closed_pnl"] == 0.0  # defaults to 0 to avoid NULL pnl
