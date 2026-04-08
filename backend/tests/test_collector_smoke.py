"""Smoke test: collect_trades_for_agent shapes rows for the new schema."""
import json
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

import collector  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "dgc_trade_sample.json"


def test_collect_trades_for_agent_uses_new_schema():
    fake_response = MagicMock()
    fake_response.json.return_value = json.loads(FIXTURE.read_text())
    fake_response.raise_for_status = MagicMock()

    sb = MagicMock()
    upsert_exec = MagicMock()
    sb.table.return_value.upsert.return_value.execute = upsert_exec

    with patch("collector.httpx.get", return_value=fake_response), \
         patch("collector.get_client", return_value=sb), \
         patch("collector.time.sleep"):
        inserted = collector.collect_trades_for_agent("137")

    # Two trades in the fixture → two upsert calls
    assert inserted == 2
    calls = sb.table.return_value.upsert.call_args_list
    assert len(calls) == 2

    first_row = calls[0].args[0]
    assert first_row["dgc_trade_id"] == "32987"
    assert first_row["entry_price"] == 0.68577
    assert first_row["leverage"] == 5
    assert first_row["opened_at_ms"] < first_row["closed_at_ms"]
    # dgc_trade_id is the natural dedup key
    assert calls[0].kwargs["on_conflict"] == "agent_id,dgc_trade_id"
