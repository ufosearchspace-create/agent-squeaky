"""Unit tests for candle_fetcher pure functions + upsert smoke test."""
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

import candle_fetcher  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "hl_candles_sample.json"


def test_candle_to_row_parses_hyperliquid_shape():
    api_candles = json.loads(FIXTURE.read_text())
    row = candle_fetcher._candle_to_row(api_candles[0])
    assert row["coin"] == "HYPE"
    assert row["interval"] == "5m"
    assert row["ts_ms"] == 1775520000000
    assert row["open"] == 36.286
    assert row["high"] == 36.319
    assert row["low"] == 36.228
    assert row["close"] == 36.272
    assert row["volume"] == 12652.2


def test_candle_to_row_handles_missing_fields():
    row = candle_fetcher._candle_to_row({"t": 123, "s": "BTC", "i": "5m"})
    assert row["ts_ms"] == 123
    assert row["open"] is None
    assert row["close"] is None
    assert row["volume"] is None


def test_select_coins_by_cumulative_coverage_picks_top_n():
    # Distribution: BTC=60, ETH=30, SOL=5, DOGE=3, PEPE=2. Cumulative 95%
    # of the 100-trade total is reached after BTC+ETH+SOL (95 trades).
    # DOGE and PEPE are the long tail we skip.
    counts = [
        {"coin": "BTC", "n": 60},
        {"coin": "ETH", "n": 30},
        {"coin": "SOL", "n": 5},
        {"coin": "DOGE", "n": 3},
        {"coin": "PEPE", "n": 2},
    ]
    selected = candle_fetcher._select_coins_by_coverage(counts, coverage=0.95)
    assert selected == ["BTC", "ETH", "SOL"]


def test_select_coins_empty_returns_empty():
    assert candle_fetcher._select_coins_by_coverage([], coverage=0.95) == []


def test_select_coins_full_coverage_returns_all():
    counts = [{"coin": "BTC", "n": 50}, {"coin": "ETH", "n": 50}]
    # 95% of 100 is 95 trades — BTC(50)+ETH(50) covers both entirely.
    selected = candle_fetcher._select_coins_by_coverage(counts, coverage=0.95)
    assert set(selected) == {"BTC", "ETH"}


def test_select_coins_ignores_zero_or_negative_counts():
    counts = [
        {"coin": "BTC", "n": 100},
        {"coin": "WEIRD", "n": 0},
        {"coin": "GARBAGE", "n": -5},
    ]
    selected = candle_fetcher._select_coins_by_coverage(counts, coverage=0.95)
    assert selected == ["BTC"]


def test_backfill_decision_small_table_means_backfill():
    assert candle_fetcher._should_backfill(existing_count=500, min_rows=1000) is True
    assert candle_fetcher._should_backfill(existing_count=0, min_rows=1000) is True


def test_backfill_decision_large_table_means_incremental():
    assert candle_fetcher._should_backfill(existing_count=5000, min_rows=1000) is False
    assert candle_fetcher._should_backfill(existing_count=1000, min_rows=1000) is False


def test_fetch_candles_calls_api_with_correct_body():
    fake_response = MagicMock()
    fake_response.json.return_value = json.loads(FIXTURE.read_text())
    fake_response.raise_for_status = MagicMock()
    with patch("candle_fetcher.httpx.post", return_value=fake_response) as post, \
         patch("candle_fetcher.time.sleep"):
        candles = candle_fetcher._fetch_candles("HYPE", "5m", 1775520000000, 1775521500000)
    assert len(candles) == 5
    post.assert_called_once()
    kwargs = post.call_args.kwargs
    assert kwargs["json"]["type"] == "candleSnapshot"
    assert kwargs["json"]["req"]["coin"] == "HYPE"
    assert kwargs["json"]["req"]["interval"] == "5m"
    assert kwargs["json"]["req"]["startTime"] == 1775520000000
    assert kwargs["json"]["req"]["endTime"] == 1775521500000


def test_fetch_candles_retries_on_429():
    # First call returns 429, second call succeeds.
    bad = MagicMock()
    bad.status_code = 429
    bad.raise_for_status.side_effect = Exception("429 Too Many")
    good = MagicMock()
    good.raise_for_status = MagicMock()
    good.json.return_value = json.loads(FIXTURE.read_text())
    with patch("candle_fetcher.httpx.post", side_effect=[bad, good]), \
         patch("candle_fetcher.time.sleep"):
        candles = candle_fetcher._fetch_candles("HYPE", "5m", 0, 1)
    assert len(candles) == 5


def test_fetch_candles_gives_up_after_max_retries():
    bad = MagicMock()
    bad.raise_for_status.side_effect = Exception("500 Server Error")
    with patch("candle_fetcher.httpx.post", return_value=bad), \
         patch("candle_fetcher.time.sleep"):
        candles = candle_fetcher._fetch_candles("HYPE", "5m", 0, 1)
    assert candles == []
