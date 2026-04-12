# Bayesian Scoring Redesign — Implementation Plan (PR1)

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Replace the weighted-average D1–D6 scorer with a Bayesian evidence-based classifier (prior `P(BOT)=0.95`, log₂-LR in bits, hard gates), including a collector schema migration, scoring engine package, signal implementations for Phases 0+1+2 (Temporal, Structural, Behavioral, Meta), frontend display of evidence waterfall, and live deployment with immediate empirical calibration against the existing 10-day dataset.

**Architecture:** See `docs/plans/2026-04-08-bayesian-scoring-redesign.md` for the full design. Approach: pure-function signal modules return `EvidenceScore(log_lr_bits, state, value, detail)`; a Bayesian aggregator sums log-LRs from the prior; hard gates run after the posterior to force-override specific classes; all LRs stored in `scanner_signal_lrs` table and loaded at analyzer startup.

**Tech Stack:** Python 3.11, Supabase Python client, numpy, scipy (new — Benford chi-square), httpx, APScheduler, Next.js 16 App Router, React 19, Tailwind 4, Recharts 3, Supabase Postgres, Railway (backend), Vercel (frontend).

**Branch:** `feat/bayesian-scoring-redesign` (already created, design doc committed in `666a692`).

**Before every `git push`:** run the audit triplet — `/cek:cek-critique`, `everything-claude-code:python-review`, `everything-claude-code:database-reviewer`. Fix all findings, re-run the flagged audit until clean.

**Golden rule:** test first, minimal code, commit often. Signal modules are **pure functions** — no DB, no HTTP, no clocks except `ctx.now_ms`. All testable with fixture dicts.

---

## Phase 0 — Collector + Schema Migration

### Task 0.1: Run schema migration 1 — scanner_trades alter

**Files:** (migration applied via Supabase MCP; also captured in `backend/migrations/001_trades_alter.sql` for audit trail)

**Step 1:** Write the SQL file `backend/migrations/001_trades_alter.sql`:

```sql
-- Phase 0: scanner_trades schema alter
ALTER TABLE scanner_trades
  ADD COLUMN IF NOT EXISTS opened_at_ms  BIGINT,
  ADD COLUMN IF NOT EXISTS closed_at_ms  BIGINT,
  ADD COLUMN IF NOT EXISTS entry_price   DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS exit_price    DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS position_size DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS leverage      SMALLINT,
  ADD COLUMN IF NOT EXISTS dgc_trade_id  TEXT,
  ADD COLUMN IF NOT EXISTS hold_time_s   INTEGER
    GENERATED ALWAYS AS ((closed_at_ms - opened_at_ms) / 1000) STORED;

CREATE UNIQUE INDEX IF NOT EXISTS scanner_trades_dgc_id_uk
  ON scanner_trades(agent_id, dgc_trade_id)
  WHERE dgc_trade_id IS NOT NULL;
```

**Step 2:** Apply via Supabase MCP `apply_migration` against project `YOUR_SUPABASE_PROJECT_ID` with name `phase0_trades_alter`.

**Step 3:** Verify with `list_tables` or direct query:

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'scanner_trades' ORDER BY ordinal_position;
```

Expected: new columns present, `hold_time_s` shows `generated: always`.

**Step 4:** Commit the SQL file:

```bash
cd "d:/Agent Squeaky"
git add backend/migrations/001_trades_alter.sql
git commit -m "chore(db): phase 0 migration — scanner_trades schema alter"
```

### Task 0.2: Run schema migration 2 — scanner_scores rewrite

**Files:** `backend/migrations/002_scores_rewrite.sql`

**Step 1:** Write the SQL file:

```sql
-- Phase 0: scanner_scores rewrite for Bayesian model
ALTER TABLE scanner_scores
  DROP COLUMN IF EXISTS d1_timing,
  DROP COLUMN IF EXISTS d2_sleep,
  DROP COLUMN IF EXISTS d3_sizing,
  DROP COLUMN IF EXISTS d4_reaction,
  DROP COLUMN IF EXISTS d5_forum,
  DROP COLUMN IF EXISTS d6_wallet,
  DROP COLUMN IF EXISTS composite;

ALTER TABLE scanner_scores
  ADD COLUMN IF NOT EXISTS prior_log_odds     DOUBLE PRECISION NOT NULL DEFAULT 4.25,
  ADD COLUMN IF NOT EXISTS posterior_log_odds DOUBLE PRECISION NOT NULL DEFAULT 4.25,
  ADD COLUMN IF NOT EXISTS p_bot              DOUBLE PRECISION NOT NULL DEFAULT 0.95,
  ADD COLUMN IF NOT EXISTS evidence_log       JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS hard_gates_hit     TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS lr_version         INTEGER NOT NULL DEFAULT 1;
```

**Step 2:** Apply via Supabase MCP (name: `phase0_scores_rewrite`).

**Step 3:** Verify columns exist. Note: defaults will be removed in a later task after the first clean run.

**Step 4:** Commit.

```bash
git add backend/migrations/002_scores_rewrite.sql
git commit -m "chore(db): phase 0 migration — scanner_scores rewrite for Bayesian"
```

### Task 0.3: Run schema migration 3 — new tables

**Files:** `backend/migrations/003_new_tables.sql`

**Step 1:** Write the full DDL for `scanner_candles`, `scanner_onchain`, `scanner_labels`, `scanner_signal_lrs` from design doc §5 Migration 3. Include `ALTER TABLE … ENABLE ROW LEVEL SECURITY` and `CREATE POLICY "Public read"` for all four.

**Step 2:** Apply via Supabase MCP (name: `phase0_new_tables`).

**Step 3:** Verify with `list_tables` that all four exist and have RLS enabled.

**Step 4:** Commit.

### Task 0.4: Run schema migration 4 — view rewrite

**Files:** `backend/migrations/004_view_rewrite.sql`

**Step 1:** Write the `DROP VIEW` + `CREATE VIEW scanner_agent_latest_scores` from design doc §5 Migration 4. Must use `WITH (security_invoker = false)`.

**Step 2:** Apply via Supabase MCP (name: `phase0_view_rewrite`).

**Step 3:** Verify: `SELECT * FROM scanner_agent_latest_scores LIMIT 1;` returns the new columns (even if evidence_log is empty before analyzer runs).

**Step 4:** Commit.

### Task 0.5: Delete reporter.py and Telegram wiring

**Files:**
- Delete: `backend/reporter.py`
- Modify: `backend/config.py`
- Modify: `backend/main.py`

**Step 1:** Delete the reporter file.

```bash
cd "d:/Agent Squeaky"
git rm backend/reporter.py
```

**Step 2:** Edit `backend/config.py` — remove `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ENABLED` lines. Final file should contain only Supabase + DGCLaw + table name constants.

**Step 3:** Edit `backend/main.py`:

```python
"""Main entry point: APScheduler runs collector + analyzer on schedule."""
import logging
import sys
from apscheduler.schedulers.blocking import BlockingScheduler

import collector
import analyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== Agent Squeaky Scanner starting ===")
    try:
        collector.run()
    except Exception:
        logger.exception("Initial collection failed")
    try:
        analyzer.run()
    except Exception:
        logger.exception("Initial analysis failed")

    scheduler = BlockingScheduler()
    scheduler.add_job(collector.run, "interval", minutes=30, id="collector")
    scheduler.add_job(analyzer.run, "interval", minutes=30, id="analyzer")
    logger.info("Scheduler started: collector=30m, analyzer=30m")
    scheduler.start()


if __name__ == "__main__":
    main()
```

**Step 4:** Commit.

```bash
git add backend/config.py backend/main.py
git commit -m "chore(backend): remove reporter/Telegram wiring"
```

### Task 0.6: Collector — write failing tests

**Files:**
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/test_collector_parse.py`
- Create: `backend/tests/fixtures/dgc_trade_sample.json`

**Step 1:** Save this sample response (from empirical API probe) as `backend/tests/fixtures/dgc_trade_sample.json`:

```json
{
  "success": true,
  "data": [
    {
      "id": "32987",
      "tradeType": "PERP_CLOSE",
      "txHash": null,
      "executedAt": "2026-04-08T14:36:34.897Z",
      "openedAt": "2026-04-08T12:55:59.124Z",
      "token": "VIRTUAL",
      "direction": "SHORT",
      "positionSize": 99.22625500000001,
      "leverage": 5,
      "entryPrice": 0.68577,
      "exitPrice": 0.67358,
      "realizedPnl": 0.917907,
      "createdAt": "2026-04-08T14:40:10.041Z"
    }
  ],
  "pagination": {"total": 1, "limit": 100, "offset": 0, "hasMore": false}
}
```

**Step 2:** Write `backend/tests/test_collector_parse.py`:

```python
"""Unit tests for collector's trade row construction from DegenClaw API."""
import json
from pathlib import Path

from collector import _trade_to_row  # new pure-function helper (does not exist yet)


FIXTURE = Path(__file__).parent / "fixtures" / "dgc_trade_sample.json"


def test_trade_to_row_parses_all_new_fields():
    api = json.loads(FIXTURE.read_text())["data"][0]
    row = _trade_to_row(agent_id="137", api_trade=api)

    assert row["agent_id"] == "137"
    assert row["dgc_trade_id"] == "32987"
    assert row["opened_at_ms"] == 1775...  # TBD real value
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
        "id": "1", "executedAt": "2026-04-08T14:00:00Z", "token": "BTC",
        "direction": "LONG", "positionSize": 100.0, "leverage": 3,
        "entryPrice": 70000, "exitPrice": 71000, "realizedPnl": 10.0,
        "openedAt": None,  # edge case
    }
    row = _trade_to_row(agent_id="X", api_trade=api)
    assert row["opened_at_ms"] is None
    assert row["closed_at_ms"] is not None
```

Fill the exact `opened_at_ms` expected value by running the ISO-to-ms conversion manually (04-08 12:55:59.124 UTC → `int(datetime(2026,4,8,12,55,59,124000,tzinfo=timezone.utc).timestamp()*1000)`). Put the real integer in the assertion.

**Step 3:** Run the test to confirm failure.

```bash
cd "d:/Agent Squeaky/backend"
python -m pytest tests/test_collector_parse.py -v
```

Expected: ImportError or AttributeError (`_trade_to_row` does not exist).

**Step 4:** Commit the failing test.

```bash
git add backend/tests/__init__.py backend/tests/fixtures/dgc_trade_sample.json backend/tests/test_collector_parse.py
git commit -m "test(collector): failing unit tests for new-field trade parsing"
```

### Task 0.7: Collector — implement `_trade_to_row` helper

**Files:** `backend/collector.py`

**Step 1:** Add at the top of `backend/collector.py` (near existing imports):

```python
from datetime import datetime, timezone


def _iso_to_ms(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def _trade_to_row(agent_id: str, api_trade: dict) -> dict:
    """Pure parser: DegenClaw API trade dict -> scanner_trades row dict."""
    return {
        "agent_id": agent_id,
        "dgc_trade_id": str(api_trade.get("id")) if api_trade.get("id") is not None else None,
        "opened_at_ms": _iso_to_ms(api_trade.get("openedAt")),
        "closed_at_ms": _iso_to_ms(api_trade.get("executedAt")),
        "coin": api_trade.get("token") or "",
        "direction": api_trade.get("direction") or "",
        "entry_price": float(api_trade["entryPrice"]) if api_trade.get("entryPrice") is not None else None,
        "exit_price":  float(api_trade["exitPrice"]) if api_trade.get("exitPrice") is not None else None,
        "position_size": float(api_trade["positionSize"]) if api_trade.get("positionSize") is not None else None,
        "leverage": int(api_trade["leverage"]) if api_trade.get("leverage") is not None else None,
        "closed_pnl": float(api_trade.get("realizedPnl") or 0),
    }
```

**Step 2:** Run tests — should now pass:

```bash
python -m pytest tests/test_collector_parse.py -v
```

Expected: 2 passed.

**Step 3:** Commit.

```bash
git add backend/collector.py
git commit -m "feat(collector): _trade_to_row pure parser for new schema fields"
```

### Task 0.8: Collector — rewrite `collect_trades_for_agent` to use new helper

**Files:** `backend/collector.py:93-161`

**Step 1:** Replace the body of `collect_trades_for_agent` — the inner loop that constructs `row = {...}` and calls `sb.table(...).upsert(...)` — with:

```python
    inserted = 0
    for t in all_trades:
        row = _trade_to_row(agent_id, t)
        if not row["dgc_trade_id"] or not row["closed_at_ms"]:
            continue  # skip malformed rows
        try:
            sb.table(TABLE_TRADES).upsert(
                row, on_conflict="agent_id,dgc_trade_id"
            ).execute()
            inserted += 1
        except Exception:
            logger.exception("Failed to insert trade for agent %s", agent_id)
    return inserted
```

Also remove the obsolete `trade_type`, `direction` (old string concat), `side`, and `size` (string) lines above.

**Step 2:** Write a smoke test `backend/tests/test_collector_smoke.py` that monkeypatches `httpx.get` to return the fixture and calls `collect_trades_for_agent("137")`. Verify it calls the Supabase client with expected row shape.

```python
from unittest.mock import MagicMock, patch
import json
from pathlib import Path

import collector

FIXTURE = Path(__file__).parent / "fixtures" / "dgc_trade_sample.json"


def test_collect_trades_for_agent_uses_new_schema():
    fake_response = MagicMock()
    fake_response.json.return_value = json.loads(FIXTURE.read_text())
    fake_response.raise_for_status = MagicMock()

    with patch("collector.httpx.get", return_value=fake_response), \
         patch("collector.get_client") as gc:
        sb = MagicMock()
        gc.return_value = sb
        collector.collect_trades_for_agent("137")

    calls = sb.table.return_value.upsert.call_args_list
    assert len(calls) == 1
    row = calls[0].args[0]
    assert row["dgc_trade_id"] == "32987"
    assert row["entry_price"] == 0.68577
    assert row["leverage"] == 5
    assert calls[0].kwargs["on_conflict"] == "agent_id,dgc_trade_id"
```

**Step 3:** Run tests — both parse and smoke should pass.

```bash
python -m pytest tests/ -v
```

**Step 4:** Commit.

```bash
git add backend/collector.py backend/tests/test_collector_smoke.py
git commit -m "feat(collector): rewrite trade upsert to use _trade_to_row + dgc_trade_id key"
```

### Task 0.9: TRUNCATE scanner_trades + scanner_scores

**Files:** (ad-hoc SQL via Supabase MCP)

**Step 1:** Run via Supabase MCP `execute_sql`:

```sql
TRUNCATE scanner_trades;
TRUNCATE scanner_scores;
```

**Step 2:** Verify both tables are empty:

```sql
SELECT COUNT(*) FROM scanner_trades;
SELECT COUNT(*) FROM scanner_scores;
```

Expected: 0 and 0.

**Step 3:** No commit — this is a data-only operation. Note the action in a session note.

### Task 0.10: Refetch all trades with new collector

**Files:** (runtime invocation)

**Step 1:** Run collector manually locally against prod DB (requires env vars):

```bash
cd "d:/Agent Squeaky/backend"
python -c "import collector; collector.run()"
```

Watch the log stream for errors. Collector should iterate all agents and paginate trades.

**Step 2:** After completion, verify row counts:

```sql
SELECT COUNT(*) AS total_trades FROM scanner_trades;
SELECT COUNT(*) FROM scanner_trades WHERE opened_at_ms IS NOT NULL;
SELECT COUNT(*) FROM scanner_trades WHERE dgc_trade_id IS NOT NULL;
SELECT MIN(opened_at_ms), MAX(closed_at_ms), AVG(hold_time_s) FROM scanner_trades;
```

Expected: all rows have `opened_at_ms` and `dgc_trade_id`, `hold_time_s` varies from near-0 to many hours, counts match.

**Step 3:** Spot-check one agent:

```sql
SELECT dgc_trade_id, coin, direction, leverage, hold_time_s, entry_price, exit_price, closed_pnl
FROM scanner_trades
WHERE agent_id = '137'
ORDER BY closed_at_ms DESC LIMIT 5;
```

Compare against a live API call (`curl ... /agents/137/trades?limit=5`).

**Step 4:** Drop the legacy columns now that every row has the new schema:

```sql
ALTER TABLE scanner_trades
  DROP COLUMN IF EXISTS side,
  DROP COLUMN IF EXISTS size,
  DROP COLUMN IF EXISTS timestamp_ms;
```

Commit the migration file:

```bash
# Add backend/migrations/005_trades_drop_legacy.sql containing the ALTER above
git add backend/migrations/005_trades_drop_legacy.sql
git commit -m "chore(db): drop legacy scanner_trades columns after refetch"
```

---

## Phase 1 — Scoring Engine Core

### Task 1.1: scoring_engine package skeleton

**Files:**
- Create: `backend/scoring_engine/__init__.py` (empty)
- Create: `backend/scoring_engine/base.py`
- Create: `backend/scoring_engine/signals/__init__.py` (empty)
- Create: `backend/tests/test_scoring_engine_base.py`

**Step 1:** Write `backend/tests/test_scoring_engine_base.py`:

```python
from scoring_engine.base import EvidenceScore, SignalContext


def test_evidence_score_is_frozen_dataclass():
    e = EvidenceScore(
        signal="T1_test", log_lr_bits=-3.0, value={"x": 1},
        state="strong_human", detail="test",
    )
    assert e.signal == "T1_test"
    assert e.log_lr_bits == -3.0
    import dataclasses
    assert dataclasses.is_dataclass(e)


def test_signal_context_holds_fields():
    ctx = SignalContext(
        agent={"id": "1"},
        trades=[],
        candles={},
        onchain=None,
        now_ms=1000,
    )
    assert ctx.agent["id"] == "1"
    assert ctx.trades == []
```

**Step 2:** Run tests → FAIL (ImportError).

**Step 3:** Write `backend/scoring_engine/base.py`:

```python
"""Core types for the Bayesian scoring engine."""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceScore:
    signal: str
    log_lr_bits: float
    value: Any
    state: str
    detail: str


@dataclass
class SignalContext:
    agent: dict
    trades: list[dict]
    candles: dict[str, list]
    onchain: dict | None
    now_ms: int
    owner_cluster: list[dict] = field(default_factory=list)
```

**Step 4:** Run tests → PASS.

**Step 5:** Commit.

```bash
git add backend/scoring_engine/ backend/tests/test_scoring_engine_base.py
git commit -m "feat(scoring): base types EvidenceScore + SignalContext"
```

### Task 1.2: Bayesian aggregator + tests

**Files:**
- Create: `backend/scoring_engine/bayesian.py`
- Create: `backend/tests/test_bayesian.py`

**Step 1:** Write `backend/tests/test_bayesian.py`:

```python
import math
from scoring_engine.base import EvidenceScore
from scoring_engine.bayesian import posterior, PRIOR_LOG_ODDS_BITS


def _ev(name, bits, state="test"):
    return EvidenceScore(signal=name, log_lr_bits=bits, value={}, state=state, detail="")


def test_prior_matches_95_5_in_bits():
    assert abs(PRIOR_LOG_ODDS_BITS - math.log2(0.95 / 0.05)) < 1e-9


def test_no_evidence_returns_prior():
    p, log_odds, log = posterior([])
    assert abs(log_odds - PRIOR_LOG_ODDS_BITS) < 1e-9
    assert log == []
    assert p > 0.94


def test_strong_human_evidence_flips_to_human():
    # prior ≈ +4.25, need at least -5.1 bits to reach HUMAN (p < 0.30)
    p, log_odds, log = posterior([
        _ev("T1", -5.0, "strong_human"),
        _ev("T2", -3.0, "strong_human"),
    ])
    assert p < 0.05
    assert len(log) == 2


def test_none_entries_are_skipped():
    p, _, log = posterior([None, _ev("T1", -1.5), None])
    assert len(log) == 1
```

**Step 2:** Run tests → FAIL.

**Step 3:** Write `backend/scoring_engine/bayesian.py`:

```python
"""Bayesian posterior over P(BOT | evidence) in log2-odds (bits)."""
import math
from .base import EvidenceScore

PRIOR_P_BOT = 0.95
PRIOR_LOG_ODDS_BITS = math.log2(PRIOR_P_BOT / (1 - PRIOR_P_BOT))


def posterior(evidence: list[EvidenceScore | None]) -> tuple[float, float, list[dict]]:
    """Return (p_bot, posterior_log_odds_bits, evidence_log_list)."""
    log_odds = PRIOR_LOG_ODDS_BITS
    log = []
    for e in evidence:
        if e is None:
            continue
        log_odds += e.log_lr_bits
        log.append({
            "signal": e.signal,
            "state": e.state,
            "log_lr_bits": round(e.log_lr_bits, 3),
            "value": e.value,
            "detail": e.detail,
        })
    p_bot = 1.0 / (1.0 + 2 ** (-log_odds))
    return p_bot, log_odds, log
```

**Step 4:** Run tests → PASS.

**Step 5:** Commit.

```bash
git add backend/scoring_engine/bayesian.py backend/tests/test_bayesian.py
git commit -m "feat(scoring): Bayesian posterior aggregator in log2-odds"
```

### Task 1.3: Classifier + tests

**Files:**
- Create: `backend/scoring_engine/classifier.py`
- Create: `backend/tests/test_classifier.py`

**Step 1:** Failing test:

```python
from scoring_engine.classifier import classify


def test_thresholds_match_design():
    assert classify(0.99) == "BOT"
    assert classify(0.97) == "BOT"
    assert classify(0.90) == "LIKELY_BOT"
    assert classify(0.70) == "UNCERTAIN"
    assert classify(0.45) == "LIKELY_HUMAN"
    assert classify(0.10) == "HUMAN"


def test_boundary_values():
    # exact thresholds resolve upward (>=)
    assert classify(0.97) == "BOT"
    assert classify(0.85) == "LIKELY_BOT"
    assert classify(0.60) == "UNCERTAIN"
    assert classify(0.30) == "LIKELY_HUMAN"
```

**Step 2:** Run → FAIL.

**Step 3:** Implement `backend/scoring_engine/classifier.py`:

```python
def classify(p_bot: float) -> str:
    if p_bot >= 0.97: return "BOT"
    if p_bot >= 0.85: return "LIKELY_BOT"
    if p_bot >= 0.60: return "UNCERTAIN"
    if p_bot >= 0.30: return "LIKELY_HUMAN"
    return "HUMAN"
```

**Step 4:** Run → PASS. **Step 5:** Commit `feat(scoring): classifier from p_bot to label`.

### Task 1.4: Calibration LR loader

**Files:**
- Create: `backend/scoring_engine/calibration.py`
- Create: `backend/tests/test_calibration.py`
- Modify: `backend/scoring_engine/calibration.py` (load from DB)

**Step 1:** Write failing test for `get_lr("T1_per_day_sleep_gap", "strong_human")` returning a float when the LR table contains seed rows. Use a mock Supabase client that returns a fixture.

```python
from unittest.mock import MagicMock, patch
from scoring_engine import calibration


def test_get_lr_loads_active_row(monkeypatch):
    fake = [
        {"signal_name": "T1_test", "version": 1,
         "thresholds": {"states": {"strong_human": -5.0, "neutral": 0.0}},
         "active": True},
    ]
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = fake
    with patch("scoring_engine.calibration.get_client", return_value=sb):
        calibration.reload_cache()
        assert calibration.get_lr("T1_test", "strong_human") == -5.0
        assert calibration.get_lr("T1_test", "neutral") == 0.0
        assert calibration.get_lr("T1_test", "missing_state") == 0.0
```

**Step 2:** Run → FAIL.

**Step 3:** Implement `backend/scoring_engine/calibration.py`:

```python
"""Loads active signal LRs from scanner_signal_lrs into an in-memory cache."""
from typing import Any
from db import get_client

_CACHE: dict[str, dict[str, float]] = {}
_VERSION: int = 0


def reload_cache() -> int:
    """Reload _CACHE from DB. Returns the max(version) observed."""
    global _CACHE, _VERSION
    sb = get_client()
    rows = sb.table("scanner_signal_lrs").select("*").eq("active", True).execute().data or []
    new_cache: dict[str, dict[str, float]] = {}
    max_v = 0
    for r in rows:
        states = (r.get("thresholds") or {}).get("states") or {}
        new_cache[r["signal_name"]] = {k: float(v) for k, v in states.items()}
        max_v = max(max_v, r.get("version") or 0)
    _CACHE = new_cache
    _VERSION = max_v
    return max_v


def get_lr(signal_name: str, state: str) -> float:
    if not _CACHE:
        reload_cache()
    return _CACHE.get(signal_name, {}).get(state, 0.0)


def current_version() -> int:
    if not _CACHE:
        reload_cache()
    return _VERSION
```

**Step 4:** Run → PASS. **Step 5:** Commit `feat(scoring): LR loader from scanner_signal_lrs`.

### Task 1.5: Seed data for `scanner_signal_lrs`

**Files:** `backend/migrations/006_seed_signal_lrs_v1.sql`

**Step 1:** Write the seed SQL inserting one row per signal from design doc §6. For each signal, `thresholds` JSONB contains a `states` object mapping state name → log_lr_bits. Example for T1:

```sql
INSERT INTO scanner_signal_lrs (signal_name, version, lr_log_bot, lr_log_human, thresholds, description, active)
VALUES
  ('T1_per_day_sleep_gap', 1, 3.0, -5.0,
   '{"states": {"strong_human": -5.0, "medium_human": -1.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0}, "cutoffs": {"strong_human_median_h": 7, "strong_human_p70_days": 0.7, "medium_human_median_h": 6, "medium_bot_max_h": 4, "strong_bot_max_h": 2, "strong_bot_p70_days": 0.7}}',
   'Per-day median sleep gap in hours',
   true),
  ('T2_sleep_window_stability', 1, 0.0, -3.0, '{"states": {"strong_human": -3.0, "medium_human": -1.5, "neutral": 0.0}, "cutoffs": {"strong_human_stddev_h": 1.5, "medium_human_stddev_h": 3.0}}', 'Circular stddev of sleep midpoint UTC hour', true),
  -- ... T3, T4, T5, T6, T7, T8
  -- ... S1..S7
  -- ... B1, B2, B3, B5
  -- ... M5
;
```

Write the full seed including all signals defined in design doc §6 (Phase 1+2 only; B4 and M1-M3, M6 come in PR2/PR3).

**Step 2:** Apply via Supabase MCP (name: `phase0_seed_signal_lrs_v1`).

**Step 3:** Verify:

```sql
SELECT signal_name, version, active, jsonb_pretty(thresholds->'states') AS states
FROM scanner_signal_lrs WHERE active ORDER BY signal_name;
```

Expected: ~20 rows (all Phase 1+2 signals).

**Step 4:** Commit `chore(db): seed signal_lrs v1 expert prior`.

### Task 1.6: Hard gates module — tests first

**Files:**
- Create: `backend/scoring_engine/gates.py`
- Create: `backend/tests/test_gates.py`

**Step 1:** Write tests for HG1, HG2, HG3 (HG4/HG5 remain no-op in PR1). Cover positive and negative cases — e.g. agent with 10 days of 22+ active hours → HG2 fires; agent with 5 days → HG2 does not fire.

Include a fixture that generates synthetic trades programmatically:

```python
def make_trades(days, hours_per_day, trades_per_hour=2):
    from datetime import datetime, timezone, timedelta
    base = datetime(2026, 3, 25, 0, 0, 0, tzinfo=timezone.utc)
    out = []
    for d in range(days):
        for h in hours_per_day:
            for _ in range(trades_per_hour):
                ts = base + timedelta(days=d, hours=h, minutes=len(out) % 60)
                out.append({
                    "closed_at_ms": int(ts.timestamp() * 1000),
                    "opened_at_ms": int(ts.timestamp() * 1000) - 60_000,
                    "coin": "BTC", "direction": "LONG",
                    "position_size": 100, "leverage": 5, "closed_pnl": 0.5,
                })
    return out
```

**Step 2:** Run → FAIL.

**Step 3:** Implement `backend/scoring_engine/gates.py`:

```python
"""Hard gates: deterministic overrides applied after Bayesian posterior."""
from collections import defaultdict
from datetime import datetime, timezone


def _per_day_active_hours(trades):
    by_day = defaultdict(set)
    for t in trades:
        dt = datetime.fromtimestamp(t["closed_at_ms"] / 1000, tz=timezone.utc)
        by_day[dt.date().isoformat()].add(dt.hour)
    return {d: len(h) for d, h in by_day.items()}


def hg2_persistent_247(trades):
    active_by_day = _per_day_active_hours(trades)
    if len(active_by_day) < 10:
        return False
    values = sorted(active_by_day.values())
    median = values[len(values) // 2]
    days_23plus = sum(1 for v in values if v >= 23)
    return median >= 22 and days_23plus >= 7


def hg3_coordinated_farm(owner_cluster):
    # Each entry is a dict with behavioral fingerprint tuple
    # Placeholder — compute L1 distances and count near-identical pairs
    if not owner_cluster or len(owner_cluster) < 4:
        return False
    # Simple: if ≥3 agents share identical (rounded) fingerprint
    fps = [tuple(round(x, 2) for x in a["fingerprint"]) for a in owner_cluster if a.get("fingerprint")]
    if len(fps) < 3:
        return False
    from collections import Counter
    return any(c >= 3 for c in Counter(fps).values())


def apply_hard_gates(agent, trades, owner_cluster, onchain, label, natural_class):
    hits = []
    if label in ("BOT", "HUMAN"):
        return label, ["gate:labeled"]
    if hg2_persistent_247(trades):
        return "BOT", ["gate:persistent_24_7"]
    if owner_cluster and hg3_coordinated_farm(owner_cluster):
        return "BOT", [f"gate:farm_{len(owner_cluster)}"]
    # HG4 and HG5 are no-op until Phase 4
    return natural_class, hits
```

**Step 4:** Run → PASS. **Step 5:** Commit `feat(scoring): hard gates HG1 HG2 HG3`.

---

## Phase 1 — Temporal signal implementations (T1–T8)

For each signal below, follow the exact same rhythm: **write failing test → implement → run → commit**. One commit per signal.

Each test file uses shared fixtures in `backend/tests/fixtures_synth.py` with helpers for generating synthetic trade timelines (human-like, bot-24/7-like, scalper, swing, etc.).

### Task 1.7: Create shared synthetic fixtures

**Files:** `backend/tests/fixtures_synth.py`

**Step 1:** Write helpers:

```python
"""Synthetic trade-timeline generators for signal testing."""
from datetime import datetime, timezone, timedelta


def _to_row(ts, coin="BTC", size=100.0, leverage=5, pnl=0.5, direction="LONG", hold_s=60):
    closed_ms = int(ts.timestamp() * 1000)
    return {
        "closed_at_ms": closed_ms,
        "opened_at_ms": closed_ms - hold_s * 1000,
        "coin": coin,
        "direction": direction,
        "position_size": size,
        "entry_price": 70000.0,
        "exit_price": 70100.0,
        "leverage": leverage,
        "closed_pnl": pnl,
        "hold_time_s": hold_s,
    }


def human_daily_trader(days=10, trades_per_active_hour=2, active_hours=(9, 10, 11, 14, 15, 16, 19, 20)):
    """Simulates a human: 8h active window, weekends lighter, variable sizes."""
    base = datetime(2026, 3, 25, 0, 0, 0, tzinfo=timezone.utc)
    out = []
    for d in range(days):
        day = base + timedelta(days=d)
        is_weekend = day.weekday() >= 5
        for h in active_hours:
            n = 1 if is_weekend else trades_per_active_hour
            for i in range(n):
                ts = day + timedelta(hours=h, minutes=i * 15)
                size = 100 + (i * 37)  # variable, mostly not round
                out.append(_to_row(ts, size=size, hold_s=3600 + (i * 117)))
    return out


def bot_24_7(days=10, trades_per_hour=5, size=99.22, leverage=5):
    base = datetime(2026, 3, 25, 0, 0, 0, tzinfo=timezone.utc)
    out = []
    for d in range(days):
        for h in range(24):
            for i in range(trades_per_hour):
                ts = base + timedelta(days=d, hours=h, minutes=i * (60 // trades_per_hour))
                out.append(_to_row(ts, size=size, leverage=leverage, hold_s=300))
    return out


def scalper_bot(n=200, coin="SOL"):
    base = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
    return [_to_row(base + timedelta(minutes=i * 3), coin=coin, size=99.37,
                     leverage=10, hold_s=45) for i in range(n)]
```

Add more generators as needed during signal implementation (e.g. `martingale_bot`, `swing_human`).

**Step 2:** Commit `test(signals): synthetic trade fixtures`.

### Task 1.8: T1 per_day_sleep_gap

**Files:**
- Create: `backend/scoring_engine/signals/temporal.py`
- Create: `backend/tests/test_signals_temporal.py`

**Step 1:** Write failing test:

```python
from scoring_engine.base import SignalContext
from scoring_engine.signals.temporal import signal_t1_per_day_sleep_gap
from tests.fixtures_synth import human_daily_trader, bot_24_7


def _ctx(trades):
    return SignalContext(agent={"id": "x"}, trades=trades, candles={}, onchain=None, now_ms=0)


def test_t1_strong_human_for_daily_8h_sleeper():
    ev = signal_t1_per_day_sleep_gap(_ctx(human_daily_trader(days=10)))
    assert ev is not None
    assert ev.state in ("strong_human", "medium_human")
    assert ev.log_lr_bits < 0


def test_t1_strong_bot_for_24_7():
    ev = signal_t1_per_day_sleep_gap(_ctx(bot_24_7(days=10)))
    assert ev is not None
    assert ev.state in ("strong_bot", "medium_bot")
    assert ev.log_lr_bits > 0


def test_t1_returns_none_with_few_days():
    ev = signal_t1_per_day_sleep_gap(_ctx(bot_24_7(days=2)))
    assert ev is None
```

**Step 2:** Run → FAIL (module/function does not exist).

**Step 3:** Implement the signal per design doc §6 T1. Use `calibration.get_lr("T1_per_day_sleep_gap", state)` to pull the LR, not a hardcoded value. Include Laplace-style safety if LR is missing (return 0.0 for state but still fill `state`).

Reference the signal implementation from design doc sekcija 3 (complete function already written there) as a starting point.

**Step 4:** Make sure tests pass; `calibration._CACHE` must be pre-seeded for the test — either call `reload_cache()` with a mocked DB client in `conftest.py`, or allow tests to monkeypatch `_CACHE` directly:

```python
# conftest.py
import pytest
from scoring_engine import calibration

@pytest.fixture(autouse=True)
def seed_lr_cache():
    calibration._CACHE = {
        "T1_per_day_sleep_gap": {
            "strong_human": -5.0, "medium_human": -1.5, "neutral": 0.0,
            "medium_bot": 1.5, "strong_bot": 3.0,
        },
        # ... all other signals as we implement them
    }
    calibration._VERSION = 1
    yield
```

**Step 5:** Commit `feat(signals): T1 per_day_sleep_gap`.

### Task 1.9 – 1.15: Signals T2, T3, T4, T5, T6, T7, T8

Follow the same TDD pattern for each:

- **T2** sleep_window_stability — needs T1 human state; uses circular stddev. Test: human 9am–9pm window → strong_human stability; irregular human → medium_human; no state return None.
- **T3** weekend_weekday_ratio — needs ≥7 days with weekend/weekday split. Test: `human_daily_trader` with weekend damping → medium_human.
- **T4** daily_volume_cv — CV of trades/day. Test: `bot_24_7` → strong_bot (uniform); human with 5+/20/3+ days → medium_human.
- **T5** dead_days — count of days with zero trades inside active window. Test: inject gap in synthetic human → medium_human.
- **T6** intraday_burst_score — % of trades in 5min 3+ cluster. Test: scalper → medium_bot.
- **T7** per_day_interval_cv — median of per-day interval CVs. Test: bot → weak_bot; human → neutral.
- **T8** ms_entropy — entropy of `closed_at_ms % 1000`. Test: inject `ts % 1000 == 0` for all → strong_bot.

One commit per signal: `feat(signals): T{N} <name>`.

Each test must cover: positive state, negative state (or neutral), and `None` return when data insufficient.

Update the `conftest.py` `seed_lr_cache` fixture to include each new signal's states as you add them.

---

## Phase 1 — Structural signals (S1–S7)

### Task 1.16 – 1.22: S1 through S7

**File:** `backend/scoring_engine/signals/structural.py` (one file, all S-signals)

Per design doc §6. Each signal: failing test → implement → pass → commit.

- **S1** round_size_pct — multiples of {10,25,50,100,250,500}. Human test: mixed round/precise sizes → medium_human.
- **S2** size_decimal_precision — `avg(len(str(size).split('.')[-1]))`. Bot test: all sizes with 6 decimals → strong_bot.
- **S3** benford_compliance — chi-square vs Benford on leading digits. Needs `scipy.stats.chisquare`. Add `scipy>=1.11` to `requirements.txt`. Bot test: uniform sizes fail Benford → medium_bot.
- **S4** coin_diversity — `unique_coins` and ratio. Bot test: 20 coins across 200 trades → strong_bot.
- **S5** size_ladder_pattern — K-means or simple clustering to detect Martingale or fixed ladder. Bot test: sizes are `{99.22, 198.44, 396.88}` only → strong_bot.
- **S6** identical_size_repetition — max frequency of any single size. Bot test: 60% of trades with `size=99.22` → strong_bot.
- **S7** leverage_variance — count of distinct `leverage` + entropy. Bot test: all `leverage=5` → medium_bot; human test: mix of 1,3,5,10,20 with entropy > 1.5 → weak_human.

Commit per signal.

---

## Phase 1 — Meta / Relative signals (M4, M5)

### Task 1.23: M5 cross_agent_behavioral_consistency

**Files:**
- Create: `backend/scoring_engine/signals/meta.py`
- Create: `backend/tests/test_signals_meta.py`

Test: build two synthetic agents with different fingerprints under same owner_cluster; M5 returns `weak_human` for the divergent one.

Implement fingerprint extraction and L1 distance calculation from SignalContext.owner_cluster. Commit.

M4 (owner_cluster_size) is metadata only — not a signal function. It is surfaced in the analyzer directly and displayed on the frontend; skip signal implementation for M4.

---

## Phase 2 — Behavioral signals (B1, B2, B3, B5)

### Task 1.24 – 1.27: B1, B2, B3, B5

**File:** `backend/scoring_engine/signals/behavioral.py`

- **B1** hold_time_distribution_variance — CV of `log(hold_time_s)`. Detect bimodal via histogram. Bot test (bimodal): 90% of trades with hold=60s, 10% with hold=3600s, 10% else → strong_bot.
- **B2** hold_time_median — weak bot for <60s or >86400s. Scalper test → weak_bot.
- **B3** win_loss_hold_asymmetry — needs ≥10 wins and ≥10 losses. Test: losses held 5× as long as wins → strong_human.
- **B5** concurrent_open_positions — compute max and median concurrent (open at close time of each trade). Test: bot managing 10 positions → strong_bot.

Each: failing test → implement → pass → commit.

---

## Phase 1 — Analyzer rewrite

### Task 1.28: analyzer.py rewrite — failing integration test first

**Files:**
- Create: `backend/tests/test_analyzer_integration.py`
- Modify: `backend/analyzer.py` (complete rewrite)

**Step 1:** Write an integration test that:
1. Mocks Supabase client to return synthetic agent + trades.
2. Mocks `calibration.get_lr` via the `seed_lr_cache` fixture.
3. Calls `analyzer.score_agent(agent)`.
4. Asserts the returned row has `p_bot`, `classification`, `evidence_log`, `hard_gates_hit`, `lr_version`.
5. For a `bot_24_7` agent, asserts `classification == "BOT"` (via HG2 or Bayesian).
6. For a `human_daily_trader` agent, asserts `classification in ("LIKELY_HUMAN", "HUMAN")`.

**Step 2:** Run → FAIL.

**Step 3:** Rewrite `backend/analyzer.py` to:

```python
"""Analyzer: Bayesian scoring of all eligible agents every 30 minutes."""
import logging, time
from datetime import datetime, timezone

from config import TABLE_AGENTS, TABLE_SCORES, TABLE_TRADES
from db import get_client
from scoring_engine.base import SignalContext
from scoring_engine.bayesian import posterior, PRIOR_LOG_ODDS_BITS
from scoring_engine.classifier import classify
from scoring_engine.gates import apply_hard_gates
from scoring_engine import calibration
from scoring_engine.signals.temporal import ALL_TEMPORAL_SIGNALS
from scoring_engine.signals.structural import ALL_STRUCTURAL_SIGNALS
from scoring_engine.signals.behavioral import ALL_BEHAVIORAL_SIGNALS
from scoring_engine.signals.meta import ALL_META_SIGNALS

logger = logging.getLogger(__name__)
MIN_TRADES = 3

ALL_SIGNALS = (
    ALL_TEMPORAL_SIGNALS
    + ALL_STRUCTURAL_SIGNALS
    + ALL_BEHAVIORAL_SIGNALS
    + ALL_META_SIGNALS
)


def _load_owner_cluster(agent):
    sb = get_client()
    if not agent.get("owner_wallet"):
        return []
    rows = (sb.table(TABLE_AGENTS).select("id,name")
            .eq("owner_wallet", agent["owner_wallet"]).execute().data or [])
    # TODO Phase 2: attach fingerprints for HG3 — fetch latest scores per sibling
    return rows


def score_agent(agent: dict) -> dict | None:
    sb = get_client()
    trades = (sb.table(TABLE_TRADES).select("*")
              .eq("agent_id", agent["id"])
              .order("closed_at_ms").execute().data or [])
    if len(trades) < MIN_TRADES:
        return None

    ctx = SignalContext(
        agent=agent,
        trades=trades,
        candles={},
        onchain=None,  # PR3
        now_ms=int(time.time() * 1000),
        owner_cluster=_load_owner_cluster(agent),
    )

    evidence = [sig(ctx) for sig in ALL_SIGNALS]
    p_bot, post_log_odds, evlog = posterior(evidence)
    natural = classify(p_bot)

    # Label override + gates
    label = None
    lrow = (sb.table("scanner_labels").select("label")
            .eq("agent_id", agent["id"]).execute().data or [])
    if lrow:
        label = lrow[0]["label"]
    final_class, gates_hit = apply_hard_gates(
        agent, trades, ctx.owner_cluster, None, label, natural
    )

    row = {
        "agent_id": agent["id"],
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "prior_log_odds": PRIOR_LOG_ODDS_BITS,
        "posterior_log_odds": post_log_odds,
        "p_bot": p_bot,
        "evidence_log": evlog,
        "hard_gates_hit": gates_hit,
        "classification": final_class,
        "lr_version": calibration.current_version(),
        "trade_count_at_scoring": len(trades),
        "flags": [],  # legacy column tolerated
    }
    sb.table(TABLE_SCORES).insert(row).execute()
    logger.info("Agent %s (%s): p=%.3f cls=%s gates=%s",
                agent["id"], agent.get("name"), p_bot, final_class, gates_hit)
    return row


def run():
    logger.info("=== Analyzer started ===")
    calibration.reload_cache()
    start = time.time()
    sb = get_client()
    agents = sb.table(TABLE_AGENTS).select("*").execute().data or []
    scored = 0
    for a in agents:
        try:
            if score_agent(a):
                scored += 1
        except Exception:
            logger.exception("Scoring failed for agent %s", a.get("id"))
    logger.info("=== Analyzer done: %d/%d scored in %.1fs ===", scored, len(agents), time.time() - start)
```

Also define `ALL_TEMPORAL_SIGNALS = [signal_t1_..., signal_t2_..., ...]` at the bottom of each signal module file.

**Step 4:** Run the integration test → PASS.

**Step 5:** Commit `feat(analyzer): Bayesian scoring pipeline rewrite`.

### Task 1.29: Remove legacy scanner_scores defaults

Once the analyzer runs successfully once in dev:

**Step 1:** Run via Supabase MCP:

```sql
ALTER TABLE scanner_scores
  ALTER COLUMN prior_log_odds DROP DEFAULT,
  ALTER COLUMN posterior_log_odds DROP DEFAULT,
  ALTER COLUMN p_bot DROP DEFAULT,
  ALTER COLUMN evidence_log DROP DEFAULT,
  ALTER COLUMN lr_version DROP DEFAULT;
```

**Step 2:** Capture in `backend/migrations/007_scores_drop_defaults.sql`. Commit.

---

## Frontend

### Task F.1: Update types.ts

**Files:** `frontend/src/lib/types.ts`

**Step 1:** Before editing, read `frontend/node_modules/next/dist/docs/` index to confirm no Next 16 API conflicts with existing pattern (per `frontend/AGENTS.md`).

**Step 2:** Replace `AgentScore`, `Trade`, add `EvidenceEntry`, `ScoreHistoryEntry` per design doc §10.

**Step 3:** Run `npm run build` in `frontend/` to confirm type-check passes (will fail in pages until they are updated; that's expected at this step).

**Step 4:** Commit `feat(frontend): new types for Bayesian scoring`.

### Task F.2: Update page.tsx

**Files:** `frontend/src/app/page.tsx`

**Step 1:** Replace composite column with P(bot) percentage column. Replace old CLASS_COLORS/BG mapping with values tied to new interpretation (BOT=dark-green, LIKELY_BOT=light-green, UNCERTAIN=gray, LIKELY_HUMAN=orange, HUMAN=red — same as before since user preference hasn't changed). Sort by `p_bot DESC` default.

**Step 2:** Rewrite the methodology section. Drop D1–D6 descriptions. Add explanation of prior 95/5, Bayesian evidence, link to `/agent/[id]` for detailed evidence waterfall.

**Step 3:** Run `npm run dev`, load `http://localhost:3000`, visually confirm. Fix any type errors.

**Step 4:** Commit `feat(frontend): dashboard rewrite for Bayesian p_bot display`.

### Task F.3: Update agent/[id]/page.tsx — Evidence Waterfall

**Files:** `frontend/src/app/agent/[id]/page.tsx`

**Step 1:** Add an `EvidenceWaterfall` component inside the file (not extracted) that renders `agent.evidence_log` as a vertical list:

```tsx
function EvidenceWaterfall({ prior, posterior, evidence }: {
  prior: number; posterior: number; evidence: EvidenceEntry[];
}) {
  const maxAbs = Math.max(5, ...evidence.map(e => Math.abs(e.log_lr_bits)));
  return (
    <div className="font-mono text-xs">
      <div className="flex items-center gap-2 py-1">
        <span className="w-48 truncate">Prior</span>
        <span className="w-16 text-right text-emerald-400">+{prior.toFixed(2)}</span>
        <div className="flex-1 h-3 bg-zinc-900 relative">
          <div className="absolute left-1/2 top-0 h-full bg-emerald-400"
               style={{ width: `${(prior / maxAbs) * 50}%` }} />
        </div>
      </div>
      {evidence.map(e => {
        const pct = (Math.abs(e.log_lr_bits) / maxAbs) * 50;
        const isBot = e.log_lr_bits > 0;
        return (
          <div key={e.signal} className="flex items-center gap-2 py-1">
            <span className="w-48 truncate" title={e.detail}>{e.signal}</span>
            <span className={`w-16 text-right ${isBot ? "text-emerald-400" : "text-red-400"}`}>
              {e.log_lr_bits > 0 ? "+" : ""}{e.log_lr_bits.toFixed(2)}
            </span>
            <div className="flex-1 h-3 bg-zinc-900 relative">
              <div className={`absolute top-0 h-full ${isBot ? "bg-emerald-400 left-1/2" : "bg-red-400 right-1/2"}`}
                   style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
      <div className="flex items-center gap-2 py-1 border-t border-zinc-800 mt-1">
        <span className="w-48 truncate font-bold">Posterior</span>
        <span className={`w-16 text-right font-bold ${posterior > 0 ? "text-emerald-400" : "text-red-400"}`}>
          {posterior > 0 ? "+" : ""}{posterior.toFixed(2)}
        </span>
      </div>
    </div>
  );
}
```

**Step 2:** Render `<EvidenceWaterfall />` after the header, before the existing charts. Pass `agent.prior_log_odds`, `agent.posterior_log_odds`, `agent.evidence_log`.

**Step 3:** Add owner info section if `agent.owner_wallet != null`:

```tsx
{agent.owner_wallet && (
  <section className="mt-6 p-4 rounded-lg border border-zinc-800 bg-zinc-900/30">
    <h2 className="text-lg font-semibold mb-2">Owner</h2>
    <p className="font-mono text-xs break-all text-zinc-500">{agent.owner_wallet}</p>
    {/* Placeholder for Phase 4: on-chain activity card */}
  </section>
)}
```

**Step 4:** Update trade table to show `hold_time_s` formatted (`formatHoldTime(s)` helper: `<60` → `"{s}s"`, `<3600` → `"{m}m {s}s"`, `<86400` → `"{h}h {m}m"`, else `"{d}d {h}h"`) and `leverage`.

**Step 5:** Run dev server, visually verify on a test agent.

**Step 6:** Commit `feat(frontend): evidence waterfall + owner info on agent detail`.

---

## Audit Phase

### Task A.1: CEK critique

**Step 1:** Invoke `/cek:cek-critique` on the entire branch diff.
**Step 2:** Fix all findings.
**Step 3:** Re-run until clean.

### Task A.2: Python review

**Step 1:** Invoke `everything-claude-code:python-review`.
**Step 2:** Fix all findings (PEP 8, type hints, Pythonic idioms, security).
**Step 3:** Re-run until clean.

### Task A.3: Database review

**Step 1:** Invoke `everything-claude-code:database-reviewer`.
**Step 2:** Fix all findings (schema, indexes, query patterns, RLS).
**Step 3:** Re-run until clean.

### Task A.4: Push and merge PR

**Step 1:** Push branch.

```bash
git push -u origin feat/bayesian-scoring-redesign
```

**Step 2:** Open PR on GitHub with title "Bayesian scoring redesign (Phase 0+1+2)". Body = copy the design doc's Problem Statement + Approach + Rollout sections.

**Step 3:** Merge to master (Railway will auto-deploy backend; Vercel needs manual `vercel --prod` from `frontend/`).

### Task A.5: Post-deploy empirical verification

**Step 1:** Check Railway logs — collector and analyzer should cycle without exceptions.

**Step 2:** Run the monitoring queries from design doc §14. Assertions:
- `SELECT COUNT(*) FROM scanner_agent_latest_scores WHERE p_bot IS NOT NULL` > 200.
- Classification distribution: LIKELY_HUMAN < 25%, HUMAN ≥ 1, UNCERTAIN < 15%.
- Spot-check: `$TRUST ME BROs` is now BOT. `ProfitReaper` is BOT or LIKELY_BOT. `ColdPotato` is LIKELY_BOT or BOT.

**Step 3:** Visit the live dashboard, verify evidence waterfall renders on a known agent.

### Task A.6: First calibration session

**Step 1:** From Claude Code session, pull top-20 candidates across classes:

```sql
SELECT id, name, p_bot, classification, hard_gates_hit, evidence_log
FROM scanner_agent_latest_scores
WHERE p_bot IS NOT NULL
ORDER BY p_bot DESC LIMIT 20;
```

**Step 2:** Manually review each, insert labels into `scanner_labels` (target: ≥10 BOT, ≥5 HUMAN, ≥5 SUSPICIOUS).

**Step 3:** Write `backend/scripts/calibrate_lrs.py` — reads labeled agents, joins with their latest evidence_log, computes per-signal empirical `P(state | BOT)` and `P(state | HUMAN)` with Laplace smoothing, clamps to ±6 bits, prints delta table, writes v2 to `scanner_signal_lrs` on confirmation.

**Step 4:** Run calibration, confirm deltas, insert v2 rows.

**Step 5:** Re-score all agents manually:

```bash
python -c "import analyzer; analyzer.run()"
```

**Step 6:** Re-run monitoring queries; confirm distribution improved.

**Step 7:** Commit `feat(scoring): calibration script + v2 LRs from labeled ground truth` (include the updated seed file).

---

## Out of scope (PR2 / PR3)

- `backend/candle_fetcher.py`, `scoring_engine/signals/reaction.py` (B4), Hyperliquid integration → PR2.
- `backend/onchain_enricher.py`, `scoring_engine/signals/onchain.py` (M1, M2, M3, M6), HG4, HG5 → PR3.
- `supply-chain-risk-auditor@trailofbits-skills` final audit → PR3.

---

## Done checklist for PR1

- [ ] All migrations 001–007 applied and committed
- [ ] `reporter.py` deleted; Telegram env vars removed
- [ ] Collector rewritten and refetches 10k+ trades with new schema
- [ ] `scoring_engine/` package with Bayesian, classifier, gates, calibration, all Phase 1+2 signals
- [ ] Analyzer rewrite, all tests green
- [ ] `scanner_signal_lrs` v1 seeded
- [ ] Frontend types, dashboard, agent detail updated
- [ ] `/cek:cek-critique` clean
- [ ] `python-review` clean
- [ ] `database-reviewer` clean
- [ ] Pushed and merged
- [ ] Deployed on Railway + Vercel
- [ ] LIKELY_HUMAN < 25%, HUMAN > 0 on live DB
- [ ] 20+ labels inserted, v2 LRs calibrated, re-scored

---

**End of implementation plan.**
