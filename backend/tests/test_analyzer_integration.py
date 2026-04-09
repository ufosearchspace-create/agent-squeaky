"""Integration test: analyzer.score_agent end-to-end with mocked Supabase."""
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

import analyzer  # noqa: E402
from tests.fixtures_synth import bot_24_7, human_daily_trader  # noqa: E402


def _build_mock_sb(trades, labels=None):
    """Supabase client double that returns ``trades`` for the trades query,
    an empty list for labels (or ``labels`` when provided), [] for owner
    cluster, and records the scores insert call for inspection.
    """
    inserted_rows = []

    class _Select:
        def __init__(self, resolver):
            self._resolver = resolver

        def eq(self, *_args, **_kwargs):
            return self

        def in_(self, *_args, **_kwargs):
            return self

        def gte(self, *_args, **_kwargs):
            return self

        def lt(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            res = MagicMock()
            res.data = self._resolver()
            return res

    class _Table:
        def __init__(self, name):
            self.name = name

        def select(self, *_args, **_kwargs):
            if self.name == "scanner_trades":
                return _Select(lambda: trades)
            if self.name == "scanner_labels":
                return _Select(lambda: labels or [])
            if self.name == "scanner_agents":
                return _Select(lambda: [])  # no siblings
            if self.name == "scanner_candles":
                return _Select(lambda: [])  # no candles in unit test
            if self.name == "scanner_onchain":
                return _Select(lambda: [])  # no onchain in unit test
            return _Select(lambda: [])

        def insert(self, row):
            inserted_rows.append(row)
            chain = MagicMock()
            chain.execute.return_value = MagicMock(data=[row])
            return chain

    sb = MagicMock()
    sb.table.side_effect = _Table
    return sb, inserted_rows


def test_score_agent_bot_24_7_becomes_bot():
    trades = bot_24_7(days=12, trades_per_hour=4)
    sb, inserted = _build_mock_sb(trades)
    with patch("analyzer.get_client", return_value=sb):
        row = analyzer.score_agent({"id": "bot1", "name": "24/7 bot", "owner_wallet": None})

    assert row is not None
    assert row["classification"] == "BOT"
    assert row["p_bot"] > 0.9
    assert len(row["evidence_log"]) >= 5  # several signals should fire
    assert "lr_version" in row
    assert inserted and inserted[0]["agent_id"] == "bot1"


def test_score_agent_human_becomes_human_ish():
    trades = human_daily_trader(days=12)
    sb, _ = _build_mock_sb(trades)
    with patch("analyzer.get_client", return_value=sb):
        row = analyzer.score_agent({"id": "h1", "name": "Real person", "owner_wallet": None})

    assert row is not None
    # With a 95/5 prior, a human needs strong evidence to actually land in
    # HUMAN/LIKELY_HUMAN — accept anything that moves below LIKELY_BOT.
    assert row["p_bot"] < 0.85
    assert row["classification"] in ("UNCERTAIN", "LIKELY_HUMAN", "HUMAN")


def test_score_agent_returns_none_for_insufficient_trades():
    sb, _ = _build_mock_sb([{"closed_at_ms": 1}])
    with patch("analyzer.get_client", return_value=sb):
        row = analyzer.score_agent({"id": "x", "name": "x", "owner_wallet": None})
    assert row is None


def test_score_agent_respects_label_override():
    trades = human_daily_trader(days=10)
    sb, inserted = _build_mock_sb(trades, labels=[{"label": "BOT"}])
    with patch("analyzer.get_client", return_value=sb):
        row = analyzer.score_agent({"id": "labeled", "name": "x", "owner_wallet": None})

    assert row["classification"] == "BOT"
    assert "gate:labeled" in row["hard_gates_hit"]
