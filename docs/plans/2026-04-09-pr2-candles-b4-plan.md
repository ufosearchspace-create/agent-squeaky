# PR2 Implementation Plan — Hyperliquid Candles + B4 Price Reaction

**Branch:** `feat/phase-3-candles-reaction`
**Goal:** Fetch Hyperliquid 5m candles for the coins Agent Squeaky agents actively trade, and introduce the B4 price-reaction signal that flags bot-level reaction lag to price spikes.

---

## Decisions locked before implementation

1. **Coin selection:** dynamic. Every 30-minute cycle, the candle_fetcher picks the minimum set of coins whose cumulative share of the last 15 days of trade volume covers ≥95%. Today that is roughly 40–60 coins out of 177 distinct. Long-tail coins are skipped — they cannot support a B4 sample anyway.
2. **Interval:** 5m only. 1m is 5× more data without a matching signal benefit at our scale.
3. **Backfill + incremental:** on first run (or when a coin has <1000 candles in the DB), do a 15-day backfill. Otherwise incremental from `MAX(ts_ms)` to `now()`.
4. **Rate limit:** 0.3 s delay between HTTP calls, exponential backoff on 429 (base 2 s, max 3 retries). Hyperliquid public API tolerates several hundred req/min; 3.3 QPS is conservative.
5. **B4 granularity:** redefined for 5 m bucket precision — we measure the fraction of agent trades whose entry lands in the same 5 m candle as a ≥0.5 % move, and the CV of the lag in candles. LR ceilings are +3.0 (medium_bot) rather than the +5.0 of the original sub-second design, reflecting the coarser resolution.
6. **TTL cleanup:** pg_cron daily at 04:00 UTC deletes candles older than 30 days. Falls back to a Python cleanup call inside `candle_fetcher.run()` if pg_cron is not enabled on Supabase.
7. **Scheduling:** candle_fetcher runs every 30 minutes, *before* analyzer in the same cycle. Analyzer then sees fresh candles for every score row.

## Signal definitions — reaction.py

### B4 price_reaction_lag (5 m bucket precision)

*Input:* `SignalContext.candles[coin] = list[dict]` with `t` (start ms), `o/h/l/c`, sorted ascending.
*Procedure for each agent:*

1. Walk the trades list. For each trade pair out of `(opened_at_ms, coin, entry_price, direction)`, find the 5 m candle that contains `opened_at_ms`.
2. A "spike" is a candle whose `(high - low) / open > 0.005` (0.5 %) OR whose return from previous close is ≥0.5 %.
3. Compute `lag_buckets = floor((opened_at_ms - spike_candle_start_ms) / 300_000)`. Lag 0 means the trade opened inside the same 5 m bucket; lag 1 is the next candle; etc.
4. Keep only `(spike_candle, trade)` pairs where `lag_buckets ≤ 3` (within 15 minutes of the spike).
5. Need ≥10 samples for the signal to run.

States:

| State | Condition | Bits |
|---|---|---|
| `strong_bot` | median_lag == 0 AND ≥70 % samples in same bucket | +3.0 |
| `medium_bot` | median_lag == 0 OR ≥50 % samples in same bucket | +1.5 |
| `weak_bot` | median_lag ≤ 1 AND ≥60 % samples in lag ≤ 1 | +0.5 |
| `weak_human` | median_lag ≥ 2 AND lag_cv > 1.0 | −0.5 |
| `neutral` | everything else | 0 |

Cutoff values live in `scanner_signal_lrs.thresholds.cutoffs`.

### B4b pre_spike_entry_rate (bonus, optional)

*Procedure:* percentage of spike-after-trade pairs where the trade opened **before** the spike (lag < 0). Indicates the agent has alpha — entered 0–1 buckets ahead of the volatility move.

States:

| State | Condition | Bits |
|---|---|---|
| `weak_bot` | `pre_spike_rate > 0.3` AND ≥10 samples | +0.5 |
| `neutral` | otherwise | 0 |

B4b is deliberately weak — humans can also have alpha and we don't want to penalize genuinely skilled traders.

## Files to create / modify

### New

- `backend/candle_fetcher.py` — Hyperliquid HTTP client + coin selector + backfill/incremental logic + `run()` entry point used by the scheduler.
- `backend/scoring_engine/signals/reaction.py` — `signal_b4_price_reaction_lag`, `signal_b4b_pre_spike_entry_rate`, `ALL_REACTION_SIGNALS`.
- `backend/tests/test_candle_fetcher.py` — unit tests on coin selection, backfill decision, parser, upsert shape (mocked httpx + Supabase client).
- `backend/tests/test_signals_reaction.py` — B4 / B4b unit tests on synthetic candles + trades.
- `backend/migrations/009_seed_b4_lrs.sql` — expert-prior LRs for B4 + B4b.
- `backend/migrations/010_candles_ttl_cron.sql` — pg_cron job for 30-day TTL (best effort; Python fallback in the fetcher itself).

### Modified

- `backend/analyzer.py` — in `score_agent`, build `candles` dict for the coins the agent actually traded and pass it to `SignalContext`. Import `ALL_REACTION_SIGNALS` and include in `ALL_SIGNALS` tuple.
- `backend/main.py` — add `candle_fetcher.run` as a job at 30 m interval, scheduled to fire 5 minutes ahead of analyzer so candles are fresh when scoring runs.
- `backend/tests/conftest.py` — extend `_V1_STATES` with B4 + B4b LR entries.

## Rate-limit and retry contract

- HTTP client: `httpx.Client(timeout=30)` (synchronous, reuse connection pool).
- Sleep `0.3 s` between every successful call.
- On HTTP 429 or 5xx: sleep `2 ** attempt` seconds, retry up to 3 times. After third failure: log the error, skip that coin, continue with the next.
- No retries on 4xx other than 429 — those are client bugs, fail loud.

## Backfill decision matrix

```
for coin in selected_coins:
    existing = SELECT COUNT(*), MAX(ts_ms) FROM scanner_candles WHERE coin = :coin AND interval = '5m'
    if existing.count < 1000:
        fetch 15 days backward from now() in chunks of ~500 candles (~41 hours each)
    else:
        fetch from existing.max_ts + 300_000 to now()
```

## TTL cleanup

```sql
-- pg_cron (if available)
SELECT cron.schedule(
  'scanner_candles_ttl',
  '0 4 * * *',
  $$ DELETE FROM scanner_candles
     WHERE ts_ms < (EXTRACT(EPOCH FROM NOW() - INTERVAL '30 days') * 1000)::bigint $$
);
```

Python fallback inside `candle_fetcher.run()` (runs once per scheduler tick, cheap check):
```python
# end of run()
sb.table("scanner_candles").delete().lt("ts_ms", cutoff_ms).execute()
```

## Success criteria

- `scanner_candles` populated with >50k rows after first Railway cycle.
- Analyzer reports `B4_price_reaction_lag` in the evidence_log for at least some agents.
- No regression: full test suite stays 89+/89 green (plus new B4 and candle_fetcher tests).
- Classification distribution should shift slightly: agents that were previously borderline LIKELY_BOT (0.85–0.90) with strong reaction bot signals should move toward BOT, and no agent should move in the opposite direction without cause.

## Audit plan

Single triplet before push: `/cek:cek-critique` + `everything-claude-code:python-review` + `everything-claude-code:database-reviewer`. Same filter rules as PR1 — high-confidence findings only.

## Explicit non-goals

- No 1m candles.
- No on-chain owner enrichment (that is PR3).
- No change to existing T/S/M/B signals.
- No Dockerfile or requirements.txt changes (httpx is already a dep).
