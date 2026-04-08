# Bayesian Scoring Redesign — Design Document

**Date:** 2026-04-08
**Author:** sasa + Claude Code brainstorming session
**Branch:** `feat/bayesian-scoring-redesign`
**Status:** Approved — ready for implementation planning

---

## 1. Problem Statement

Agent Squeaky currently classifies DegenClaw competitors as BOT/HUMAN using a weighted average of six dimensions (D1–D6). Empirical analysis of 219 scored agents / 10,395 trades revealed fundamental issues:

| Class | Count | % |
|---|---|---|
| BOT | 47 | 21% |
| LIKELY_BOT | 33 | 15% |
| UNCERTAIN | 15 | 7% |
| **LIKELY_HUMAN** | **124** | **57%** |
| HUMAN | 0 | 0% |

**Critical diagnoses:**

1. **Wrong prior.** DegenClaw is an AI agent arena — baseline expectation is BOT. Weighted average implicitly uses a 50/50 prior, producing a massive bias toward LIKELY_HUMAN (57%) and making HUMAN unreachable (0 agents).
2. **Single-signal dominance.** With weighted average, one maxed-out "human" signal (e.g. D1=1.00) drags an otherwise obvious bot into LIKELY_HUMAN. 58% of agents have `d1_timing >= 0.95` — D1 no longer discriminates, and yet it pulls everyone up.
3. **Broken signals.** D4 is stuck at 0.35–0.55 for 74% of agents (carries no information). D5 (PnL) is noisy. D1 CV filter is defeated by modern bots randomizing intervals.
4. **Aggregated hour coverage hides real sleep gaps.** D2 aggregates trade hours across all days. An agent with 4h sleep gaps every day aggregates into "17 active hours" = weak signal, when per-day analysis would show consistent bot behavior.
5. **Data discarded.** Collector receives `openedAt`, `entryPrice`, `exitPrice`, `leverage` from DegenClaw API but throws them away. Hold time is computable but unused.
6. **Owner clustering invisible.** 273 agents share 157 owners (116 agents share owners with others). One wallet (`denisigin.eth`) runs 8 agents with coordinated strategies — an obvious bot farm that current scoring treats individually.
7. **Wallet age stored on the wrong entity.** `scanner_agents.first_seen` measures when we saw them, not on-chain age. Agent wallets are all fresh Alchemy AA proxies (verified empirically — Fat Tiger agent is 7 days old, ERC-4337 smart contract). Owner wallets carry real signal (empirically: `denisigin.eth` is 715 days old with 30 tx + 34 NFTs + DeFi activity).

**Target:** detect "cheaters" — programmers providing signals or manual trades in an AI agent arena. Since programmers can fake any single signal, only multi-evidence convergence from hard-to-fake signals is reliable.

## 2. Approach: Bayesian Evidence Accumulation

Replace weighted average with a Bayesian posterior over `P(BOT | evidence)`:

- **Prior:** `P(BOT) = 0.95` (`prior_log_odds = log₂(0.95 / 0.05) ≈ +4.25 bits`).
- **Likelihood ratio per signal:** `LR_s = P(signal_s observed | BOT) / P(signal_s observed | HUMAN)`, stored as `log₂(LR)` ("bits") in base-2.
- **Posterior update:** `posterior_log_odds = prior_log_odds + Σ log_lr_bits(s)`.
- **Final:** `P(BOT) = 1 / (1 + 2^(-posterior_log_odds))`.

**Why log-odds in bits:**
- Adding evidence = summing log-LRs (fast, associative, stable).
- No underflow on tiny probabilities.
- Human-readable ("this signal is +3 bits = 8:1 odds for bot").
- Signal with highest `|log_lr|` is the strongest contributor, visible in evidence log.

**Signal strength categories (expert prior v1):**

| Class | bits | Interpretation |
|---|---|---|
| very_strong | ±5.0 | 32:1 |
| strong | ±3.0 | 8:1 |
| medium | ±1.5 | 2.8:1 |
| weak | ±0.5 | 1.4:1 |
| neutral | 0.0 | inapplicable |

**Hard gates** apply *after* the Bayesian calculation and can override classification without touching the probability (for reproducibility):

- **HG1** — agent is in `scanner_labels` → use that label.
- **HG2** — collection period ≥ 10 days AND median active hours/day ≥ 22 AND ≥7 days with 23+ active hours → force `BOT`.
- **HG3** — owner has ≥4 agents AND ≥3 share near-identical behavioral fingerprints (L1 distance < 0.2) → force `BOT` for all in the cluster.
- **HG4** — (Phase 4) owner on-chain: age ≥ 365d + 5+ chains + activity_score > 0.7 → cap classification at `UNCERTAIN` (ceiling, not force).
- **HG5** — (Phase 4) owner age < 14d + defi_interaction_count < 3 + agent has ≥50 trades within 5 days of first_seen → force `BOT`.

## 3. Classification Thresholds

| `P(BOT)` | Class |
|---|---|
| ≥ 0.97 | BOT |
| 0.85 – 0.97 | LIKELY_BOT |
| 0.60 – 0.85 | UNCERTAIN |
| 0.30 – 0.60 | LIKELY_HUMAN |
| < 0.30 | HUMAN |

An agent with **zero evidence** stays at the prior `P(BOT) = 0.95` → `LIKELY_BOT`. To drop below `0.85` (UNCERTAIN) requires ≥ −1.3 bits human evidence. To reach `HUMAN` (< 0.30) requires ≥ −5.1 bits. This solves the "57% default LIKELY_HUMAN" bug: multi-dimensional converging human evidence is required to cross the threshold.

## 4. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                          BACKEND (Railway)                         │
│                                                                    │
│  collector.py  (Phase 0 fix)                                       │
│    ├─ /leaderboard → scanner_agents                                │
│    └─ /agents/{id}/trades → scanner_trades                         │
│        now stores: openedAt, entryPrice, exitPrice, leverage,      │
│                    dgc_trade_id                                    │
│                                                                    │
│  candle_fetcher.py  (Phase 3 — later PR)                           │
│    └─ Hyperliquid candleSnapshot → scanner_candles                 │
│                                                                    │
│  onchain_enricher.py  (Phase 4 — later PR)                         │
│    └─ Etherscan V2 / Dune → scanner_onchain                        │
│                                                                    │
│  scoring_engine/                                                   │
│    base.py         EvidenceScore, SignalContext                    │
│    calibration.py  LR loader (active scanner_signal_lrs rows)      │
│    bayesian.py     posterior() aggregator                          │
│    gates.py        HG1..HG5 hard gates                             │
│    classifier.py   p_bot → label mapping                           │
│    signals/                                                        │
│      temporal.py    T1..T8                                         │
│      structural.py  S1..S7                                         │
│      behavioral.py  B1..B5 (Phase 2, available immediately after   │
│                     Phase 0 refetch)                               │
│      reaction.py    B4, B4b (Phase 3 — later PR)                   │
│      meta.py        M1..M6 (Phase 4 — later PR)                    │
│      relative.py    M4, M5                                         │
│                                                                    │
│  analyzer.py  (rewritten)                                          │
│    loads agent + trades (+ candles + onchain when present)         │
│    runs all signal modules → list[EvidenceScore]                   │
│    calls bayesian.posterior + gates → writes scanner_scores row    │
│    with full evidence_log as jsonb                                 │
│                                                                    │
│  main.py   (simplified — no reporter)                              │
│    BlockingScheduler: collector.run every 30m, analyzer.run 30m   │
│    Phase 3+4 will add candle_fetcher and onchain_enricher jobs    │
│                                                                    │
│  reporter.py  →  DELETED (Telegram out)                            │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                       SUPABASE (Postgres)                          │
│                                                                    │
│  Modified:                                                         │
│    scanner_trades                                                  │
│      + opened_at_ms, closed_at_ms (rename from timestamp_ms)       │
│      + entry_price, exit_price                                     │
│      + position_size (float, replaces text size)                   │
│      + leverage                                                    │
│      + hold_time_s (GENERATED column)                              │
│      + dgc_trade_id (natural dedup key)                            │
│      drop: side, size (after analyzer rewrite)                     │
│                                                                    │
│    scanner_scores                                                  │
│      drop: d1..d6, composite                                       │
│      + prior_log_odds, posterior_log_odds, p_bot                   │
│      + evidence_log (jsonb)                                        │
│      + hard_gates_hit (text[])                                     │
│      + lr_version                                                  │
│                                                                    │
│    scanner_agent_latest_scores (VIEW — recreated)                  │
│      joins scanner_agents + latest scanner_scores                  │
│      + owner_agents_count (cross-join subquery)                    │
│      + owner_age_days, owner_activity_score from scanner_onchain   │
│      ordered by p_bot DESC                                         │
│                                                                    │
│  New:                                                              │
│    scanner_candles       — Hyperliquid OHLCV cache (Phase 3)      │
│    scanner_onchain       — Owner wallet enrichment (Phase 4)      │
│    scanner_labels        — Manual ground-truth labels              │
│    scanner_signal_lrs    — Versioned signal LRs (seed + calib)     │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                         FRONTEND (Vercel)                         │
│                                                                    │
│  / (page.tsx)                                                      │
│    column changes: P(bot) replaces composite; evidence summary     │
│    sort by p_bot DESC default                                      │
│    hard gate filter chips                                          │
│    methodology section fully rewritten                             │
│                                                                    │
│  /agent/[id] (page.tsx)                                            │
│    NEW: Evidence Waterfall — signal list with bit contributions    │
│    NEW: Owner info + on-chain profile (when available)             │
│    existing charts kept: interval histogram, hourly activity,      │
│                           position sizes scatter                   │
│    trade table adds hold_time_s + leverage columns                 │
│                                                                    │
│  No /labels page — labeling is done via Supabase MCP directly.    │
└────────────────────────────────────────────────────────────────────┘
```

## 5. Database Schema Changes

### Migration 1 — Phase 0 (PR1)

```sql
-- scanner_trades: add new fields, keep legacy for one release
ALTER TABLE scanner_trades
  ADD COLUMN opened_at_ms  BIGINT,
  ADD COLUMN closed_at_ms  BIGINT,
  ADD COLUMN entry_price   DOUBLE PRECISION,
  ADD COLUMN exit_price    DOUBLE PRECISION,
  ADD COLUMN position_size DOUBLE PRECISION,
  ADD COLUMN leverage      SMALLINT,
  ADD COLUMN dgc_trade_id  TEXT;

-- Backfill + then drop the legacy columns via TRUNCATE+refetch strategy (see §8).
-- After refetch, 'timestamp_ms' equals 'closed_at_ms'; we then drop side and text size.

-- hold_time_s as a stored generated column (Postgres 12+)
ALTER TABLE scanner_trades
  ADD COLUMN hold_time_s INTEGER
    GENERATED ALWAYS AS ((closed_at_ms - opened_at_ms) / 1000) STORED;

-- Natural dedup key via DegenClaw's own id
CREATE UNIQUE INDEX scanner_trades_dgc_id_uk
  ON scanner_trades(agent_id, dgc_trade_id)
  WHERE dgc_trade_id IS NOT NULL;

-- After TRUNCATE + refetch cycle confirms new rows have dgc_trade_id:
--   ALTER TABLE scanner_trades DROP COLUMN side;
--   ALTER TABLE scanner_trades DROP COLUMN size;
--   ALTER TABLE scanner_trades DROP COLUMN timestamp_ms;
```

### Migration 2 — scanner_scores rewrite (PR1)

```sql
ALTER TABLE scanner_scores DROP COLUMN d1_timing;
ALTER TABLE scanner_scores DROP COLUMN d2_sleep;
ALTER TABLE scanner_scores DROP COLUMN d3_sizing;
ALTER TABLE scanner_scores DROP COLUMN d4_reaction;
ALTER TABLE scanner_scores DROP COLUMN d5_forum;
ALTER TABLE scanner_scores DROP COLUMN d6_wallet;
ALTER TABLE scanner_scores DROP COLUMN composite;

ALTER TABLE scanner_scores
  ADD COLUMN prior_log_odds      DOUBLE PRECISION NOT NULL DEFAULT 4.25,
  ADD COLUMN posterior_log_odds  DOUBLE PRECISION NOT NULL DEFAULT 4.25,
  ADD COLUMN p_bot               DOUBLE PRECISION NOT NULL DEFAULT 0.95,
  ADD COLUMN evidence_log        JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN hard_gates_hit      TEXT[] DEFAULT '{}',
  ADD COLUMN lr_version          INTEGER NOT NULL DEFAULT 1;

-- After first successful run the DEFAULTs come off:
ALTER TABLE scanner_scores
  ALTER COLUMN prior_log_odds DROP DEFAULT,
  ALTER COLUMN posterior_log_odds DROP DEFAULT,
  ALTER COLUMN p_bot DROP DEFAULT,
  ALTER COLUMN evidence_log DROP DEFAULT,
  ALTER COLUMN lr_version DROP DEFAULT;
```

### Migration 3 — New tables (PR1)

```sql
-- Hyperliquid OHLCV cache (Phase 3 fills it — schema created in PR1)
CREATE TABLE scanner_candles (
  coin       TEXT NOT NULL,
  interval   TEXT NOT NULL,
  ts_ms      BIGINT NOT NULL,
  open       DOUBLE PRECISION,
  high       DOUBLE PRECISION,
  low        DOUBLE PRECISION,
  close      DOUBLE PRECISION,
  volume     DOUBLE PRECISION,
  fetched_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (coin, interval, ts_ms)
);
CREATE INDEX scanner_candles_coin_ts ON scanner_candles(coin, ts_ms);
ALTER TABLE scanner_candles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON scanner_candles FOR SELECT USING (true);

-- Owner wallet enrichment (Phase 4 fills it — schema created in PR1)
CREATE TABLE scanner_onchain (
  owner_wallet            TEXT PRIMARY KEY,
  base_first_tx_date      DATE,
  age_days                INTEGER,
  total_tx_count          INTEGER,
  chains_active           SMALLINT,
  defi_interaction_count  INTEGER,
  nft_count               INTEGER,
  has_ens                 BOOLEAN DEFAULT FALSE,
  activity_score          REAL,
  last_refreshed_at       TIMESTAMPTZ DEFAULT NOW(),
  source                  TEXT
);
ALTER TABLE scanner_onchain ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON scanner_onchain FOR SELECT USING (true);

-- Manual labels (ground truth for calibration)
CREATE TABLE scanner_labels (
  agent_id    TEXT PRIMARY KEY REFERENCES scanner_agents(id),
  label       TEXT NOT NULL CHECK (label IN ('BOT','HUMAN','SUSPICIOUS','UNSURE')),
  reason      TEXT,
  labeled_by  TEXT DEFAULT 'sasa',
  labeled_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE scanner_labels ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON scanner_labels FOR SELECT USING (true);

-- Versioned signal likelihood ratios
CREATE TABLE scanner_signal_lrs (
  signal_name    TEXT NOT NULL,
  version        INTEGER NOT NULL,
  lr_log_bot     DOUBLE PRECISION NOT NULL,
  lr_log_human   DOUBLE PRECISION NOT NULL,
  thresholds     JSONB,
  description    TEXT,
  calibrated_at  TIMESTAMPTZ DEFAULT NOW(),
  calibrated_by  TEXT DEFAULT 'expert-prior',
  active         BOOLEAN DEFAULT TRUE,
  PRIMARY KEY (signal_name, version)
);
CREATE INDEX scanner_signal_lrs_active
  ON scanner_signal_lrs(signal_name) WHERE active;
ALTER TABLE scanner_signal_lrs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON scanner_signal_lrs FOR SELECT USING (true);
```

### Migration 4 — View (PR1)

```sql
DROP VIEW IF EXISTS scanner_agent_latest_scores;

CREATE VIEW scanner_agent_latest_scores
WITH (security_invoker = false)
AS
SELECT
  a.id, a.name, a.wallet_address, a.owner_wallet,
  a.total_pnl, a.trade_count, a.win_rate,
  s.prior_log_odds, s.posterior_log_odds, s.p_bot,
  s.evidence_log, s.hard_gates_hit, s.classification,
  s.lr_version, s.scored_at, s.trade_count_at_scoring,
  (SELECT COUNT(*) FROM scanner_agents o
   WHERE o.owner_wallet = a.owner_wallet) AS owner_agents_count,
  oc.age_days AS owner_age_days,
  oc.activity_score AS owner_activity_score,
  oc.chains_active AS owner_chains_active
FROM scanner_agents a
LEFT JOIN LATERAL (
  SELECT * FROM scanner_scores sc
  WHERE sc.agent_id = a.id
  ORDER BY sc.scored_at DESC
  LIMIT 1
) s ON true
LEFT JOIN scanner_onchain oc ON oc.owner_wallet = a.owner_wallet
ORDER BY s.p_bot DESC NULLS LAST, a.trade_count DESC;
```

## 6. Signal Inventory

Signals implemented in PR1 (Phase 0 + 1 + 2). Each signal module is a pure function: `(SignalContext) -> EvidenceScore | None`. `None` means insufficient data (not counted in posterior); `neutral` state means applicable but carries zero evidence.

LR values in bits. `+` = evidence for BOT, `-` = evidence for HUMAN.

### Temporal (T-series)

| Signal | States (cutoffs) | LR (bits) |
|---|---|---|
| **T1 per_day_sleep_gap** | needs ≥5 days with ≥3 trades/day. `longest_gap` per day, wrap-around 48h. | strong_human (median ≥7h AND ≥70% days ≥8h): **−5.0** · medium_human (median ≥6h): **−1.5** · neutral: 0 · medium_bot (median ≤4h): **+1.5** · strong_bot (median ≤2h AND ≥70% days ≤3h): **+3.0** |
| **T2 sleep_window_stability** | only applies if T1 is `_human`. Circular stddev of sleep-midpoint UTC hour. | strong_human (stddev <1.5h): **−3.0** · medium_human (stddev <3h): **−1.5** · neutral: 0 |
| **T3 weekend_weekday_ratio** | needs ≥7 days, ≥2 weekend days, ≥3 weekdays. | medium_human (ratio <0.5): **−1.5** · weak_human (<0.75): **−0.5** · neutral · weak_bot (>1.25): **+0.5** |
| **T4 daily_volume_cv** | needs ≥7 days. CV of trades/day. | medium_human (CV >1.0): **−1.5** · weak_human (>0.6): **−0.5** · neutral · medium_bot (<0.3): **+1.5** · strong_bot (<0.15): **+3.0** |
| **T5 dead_days** | needs ≥10 days. Days inside active window with zero trades. | medium_human (≥2): **−1.5** · weak_human (=1): **−0.5** · neutral · medium_bot (0 dead AND ≥14 active days): **+1.5** |
| **T6 intraday_burst_score** | needs ≥20 trades. % trades in 5min-window 3+ clusters. | weak_bot (>0.4): **+0.5** · medium_bot (>0.6): **+1.5** · neutral |
| **T7 per_day_interval_cv** | needs ≥5 days with ≥5 trades/day. Median of per-day interval CVs. | weak_bot (<0.3): **+0.5** · neutral |
| **T8 ms_entropy** | needs ≥20 trades. `closed_at_ms % 1000` distribution entropy. | strong_bot (>80% end in .000): **+3.0** · medium_bot (entropy <3 bits): **+1.5** · neutral (entropy ≥5 bits) |

### Structural (S-series)

| Signal | States | LR (bits) |
|---|---|---|
| **S1 round_size_pct** | % sizes divisible by {10,25,50,100,250,500}. | medium_human (>0.4): **−1.5** · weak_human (>0.2): **−0.5** · neutral · weak_bot (<0.05): **+0.5** |
| **S2 size_decimal_precision** | avg decimal places of `position_size`. | strong_bot (≥5): **+3.0** · medium_bot (≥4): **+1.5** · neutral (<3) |
| **S3 benford_compliance** | needs ≥30 trades. Chi-square test vs Benford on leading digits. | weak_human (p>0.3): **−0.5** · neutral · medium_bot (p<0.05): **+1.5** |
| **S4 coin_diversity** | needs ≥10 trades. `unique_coins` absolute + ratio. | strong_bot (≥15 coins AND ratio >0.2): **+3.0** · medium_bot (≥10 coins): **+1.5** · weak_human (≤3 coins AND ≥20 trades): **−0.5** · neutral |
| **S5 size_ladder_pattern** | needs ≥20 trades. Cluster sizes; detect Martingale (size doubles after loss) or ≤3 fixed clusters covering >70%. | strong_bot (≤3 clusters cover >70%): **+3.0** · medium_bot (Martingale detected): **+1.5** · neutral |
| **S6 identical_size_repetition** | Max frequency of any single size value. | strong_bot (>0.5): **+3.0** · medium_bot (>0.3): **+1.5** · neutral (<0.2) · weak_human (unique_ratio >0.95 AND ≥30 trades): **−0.5** |
| **S7 leverage_variance** | needs ≥15 trades with `leverage` populated. Distinct leverage count + entropy. | medium_bot (1 distinct): **+1.5** · weak_bot (2 distinct, dominant >90%): **+0.5** · neutral · weak_human (≥4 distinct AND entropy >1.5 bits): **−0.5** |

### Behavioral (B-series) — Phase 2, enabled immediately after Phase 0 refetch

| Signal | States | LR (bits) |
|---|---|---|
| **B1 hold_time_distribution_variance** | needs ≥20 trades with `hold_time_s`. CV of `log(hold_time_s)`. | medium_human (log_cv >1.2): **−1.5** · neutral · medium_bot (log_cv <0.3): **+1.5** · strong_bot (bimodal, 90%+ in 2 tight clusters): **+3.0** |
| **B2 hold_time_median** | median hold_time_s. | weak_bot (<60s scalper): **+0.5** · weak_bot (>86400s swing-fixed): **+0.5** · neutral |
| **B3 win_loss_hold_asymmetry** | needs ≥10 wins AND ≥10 losses. `median_hold_losses / median_hold_wins`. | strong_human (>2.0 — humans hold losers): **−3.0** · medium_human (>1.5): **−1.5** · neutral · medium_bot (0.9–1.1 symmetric): **+1.5** · weak_bot (<0.7 disciplined cut-losses): **+0.5** |
| **B5 concurrent_open_positions** | compute open-position count over trade timeline. Median and max. | strong_bot (max ≥10 OR median ≥5): **+3.0** · medium_bot (max ≥6 OR median ≥3): **+1.5** · neutral · weak_human (median =1 AND max ≤2): **−0.5** |

### Meta / Relative (M-series)

| Signal | States | LR (bits) |
|---|---|---|
| **M4 owner_cluster_size** | metadata only — not in posterior. Display as "runs N other agents" on `/agent/[id]`. | — |
| **M5 cross_agent_behavioral_consistency** | needs owner with ≥2 agents. L1 distance of behavioral fingerprint `(T1 median_gap, S1 round_pct, S6 max_single_freq, S2 avg_decimals, S7 dominant_lev)` from cluster centroid. | weak_human (distance >0.4 — divergent from cluster): **−0.5** · neutral · (no bot state — homogeneity is expected) |

### Phase 3 signals (PR2 — not in PR1)

- **B4 price_spike_reaction_lag** — needs Hyperliquid candles. Very strong (±5.0) bot signal when median lag < 3s with low CV.
- **B4b pre_spike_entry_rate** — weak bonus signal.

### Phase 4 signals (PR3 — not in PR1)

- **M1 owner_wallet_age** — `scanner_onchain.age_days`. Weak-to-medium human evidence for old wallets.
- **M2 owner_multi_chain_presence** — `chains_active`.
- **M3 owner_defi_nft_diversity** — composite `activity_score`.
- **M6 throwaway_owner_flag** — age < 14d + defi < 3 + nft = 0.

### Out of scope for v2

- R1 timing_correlation_with_other_agents (cross-agent matrix, expensive)
- B6 pnl_per_trade_cv (overlaps with B3)
- B7 trade_cancellation_rate (DegenClaw does not expose)
- B8 direction_flip (weak, overlaps with B1)

## 7. Hard Gates

Applied after Bayesian posterior is computed. Final classification is:

```
1. HG1 (labeled) — absolute priority
2. HG2, HG3, HG5 (force BOT) — next
3. HG4 (ceiling) — applies only if none above fired
4. Otherwise: natural classification from p_bot
```

HG4 and HG5 are inert until Phase 4 lands.

## 8. Phase 0 Backfill Strategy

DegenClaw API returns complete open+close pairs per row, so Phase 0 needs no schema archaeology. We refetch everything cleanly:

```sql
TRUNCATE scanner_trades;
TRUNCATE scanner_scores;
-- scanner_agents and scanner_forum_posts are preserved.
```

Then `python -c "import collector; collector.run()"` executed once on Railway to rehydrate. DegenClaw API's `pagination.total` per agent confirms we get full history (test on agent 137 returned 144 trades over 7 days, consistent with a 10-day retention window).

Post-refetch verification:
- `SELECT COUNT(*) FROM scanner_trades` should be ≥ previous count (we were deduping aggressively).
- `SELECT COUNT(*) FROM scanner_trades WHERE opened_at_ms IS NOT NULL` should equal total — every row must have opened_at from the API response.
- `SELECT AVG(hold_time_s), MIN(hold_time_s), MAX(hold_time_s) FROM scanner_trades` to confirm the GENERATED column works.

After two clean 30-minute collector cycles, drop legacy columns:

```sql
ALTER TABLE scanner_trades
  DROP COLUMN side,
  DROP COLUMN size,
  DROP COLUMN timestamp_ms;
```

## 9. Calibration Workflow (Option C — expert prior + iterative)

1. **Seed `scanner_signal_lrs` (version 1, `calibrated_by = 'expert-prior-v1'`, `active = true`)** with the cutoffs and LRs from §6.
2. **First live run** of the analyzer produces `scanner_scores` rows with the expert-prior LRs.
3. **Ground-truth labeling** via Claude Code session: insert 20–30 `scanner_labels` rows (≥10 BOT, ≥5 HUMAN, ≥5 SUSPICIOUS) based on empirical review of top candidates.
4. **Run `scripts/calibrate_lrs.py`** — computes empirical `P(state | BOT)` and `P(state | HUMAN)` with Laplace smoothing, clamps LR to ±6 bits, prints delta vs v1. Human approves each signal's delta before inserting v2.
5. **Re-score** all agents after LR update. Classification history preserved in `scanner_scores`.
6. **Evaluate distribution** — target LIKELY_HUMAN < 25%, HUMAN > 0 members, UNCERTAIN < 15%. If the re-score flips too many agents aggressively, add more labels and iterate.

## 10. Frontend Changes

### `/` (page.tsx)

- Columns: Name · PnL · Trades · **P(bot) %** · Class · Evidence summary.
- Sort by `p_bot DESC` by default.
- Filter chips: classes + hard gate filters (`gate:labeled`, `gate:persistent_24_7`, `gate:farm`, `gate:onchain_human_ceiling`, `gate:throwaway_farm`).
- Methodology section rewritten to explain prior, Bayesian evidence, evidence waterfall.
- Disclaimer updated: "Default assumption is BOT. HUMAN classification requires multi-dimensional converging evidence."

### `/agent/[id]` (page.tsx)

- New **Evidence Waterfall** — list `evidence_log` entries, horizontal bars width ∝ `|log_lr_bits|`, red-left (human) / green-right (bot). Tooltip shows `value` + `detail`.
- New **Owner Info** section — shown when `owner_wallet != null`:
  - if cluster size > 1 → "runs N other agents" with links and class badges.
  - if `scanner_onchain` row present → wallet age, tx count, chains, DeFi/NFT counts, ENS, `activity_score`.
- Existing charts kept (interval histogram, hourly activity, size scatter).
- Trade table adds `hold_time_s` (formatted) + `leverage`.
- Score history table shows `lr_version` + `p_bot` trajectory.

### `src/lib/types.ts`

`AgentScore` replaced with the interface defined in §Design Sekcija 6 (contains `p_bot`, `prior_log_odds`, `posterior_log_odds`, `evidence_log: EvidenceEntry[]`, `hard_gates_hit`, `lr_version`, `owner_wallet`, owner cluster + onchain fields).

`Trade` interface updated to include `opened_at_ms`, `closed_at_ms`, `hold_time_s`, `entry_price`, `exit_price`, `position_size`, `leverage`, `direction` (LONG/SHORT), `dgc_trade_id`.

**Next.js 16 note:** before any code change in `frontend/`, read the relevant docs in `frontend/node_modules/next/dist/docs/` (per `frontend/AGENTS.md`) — Next 16 has breaking changes and this design must not rely on memorized Next API.

## 11. Rollout Plan

### PR1 — Big Bang (Phase 0 + 1 + 2) — today

Branch: `feat/bayesian-scoring-redesign`

1. Migrations 1–4 via Supabase MCP `apply_migration`.
2. Delete `backend/reporter.py`. Remove Telegram config from `backend/config.py`. Remove Telegram env vars from Railway.
3. `backend/main.py` simplified — no `analysis_cycle`, no daily_summary cron.
4. Collector rewrite (new fields, `dgc_trade_id` upsert key).
5. `backend/scoring_engine/` package full implementation (signals T1–T8, S1–S7, M4, M5, B1–B5, Bayesian engine, classifier, gates HG1–HG3).
6. Analyzer rewrite.
7. SQL seed for `scanner_signal_lrs` v1.
8. Frontend changes (types, page, agent detail, view).
9. Unit tests per signal + bayesian + gates + integration test.
10. **Audit triplet** before push: `/cek:cek-critique` + `everything-claude-code:python-review` + `everything-claude-code:database-reviewer`. Fix all findings, re-run until clean.
11. Commit on `feat/bayesian-scoring-redesign`, push, open PR to master.
12. Merge PR, deploy (Railway auto-deploys from master; `vercel --prod` for frontend).
13. **Empirical verification post-deploy:**
    - `SELECT COUNT(*) FROM scanner_trades` should rebuild after refetch.
    - Distribution query: LIKELY_HUMAN should drop below 25%, HUMAN > 0.
    - Known misclassified agents (`$TRUST ME BROs`, `ProfitReaper`, `ColdPotato`) should become BOT/LIKELY_BOT.
14. **Calibration session:** label 20–30 obvious agents → run calibration script → review delta → insert v2 LRs → re-score → confirm distribution shifts as expected.

### PR2 — Phase 3 (candles + B4) — follow-up

Branch: `feat/phase-3-candles-reaction`
- `backend/candle_fetcher.py` Hyperliquid integration
- Candle backfill (10 days × top 15 coins × 5m = ~43k rows one-time)
- Scheduler job every 60m
- `backend/scoring_engine/signals/reaction.py` implements B4, B4b
- `scanner_signal_lrs` seed append
- Audit triplet before push
- TTL cleanup cron (pg_cron or Python job)

### PR3 — Phase 4 (onchain + HG4/HG5) — follow-up

Branch: `feat/phase-4-onchain`
- `backend/onchain_enricher.py` Etherscan V2 integration (needs API key)
- `backend/scoring_engine/signals/meta.py` implements M1, M2, M3, M6
- HG4, HG5 gates activated
- Audit quartet on final push: triplet + `supply-chain-risk-auditor@trailofbits-skills` (cumulative dependency audit)

## 12. Audit Policy

Before **every** push (PR1, PR2, PR3):

1. **`/cek:cek-critique`** — mandatory multi-perspective CEK audit (user rule).
2. **`everything-claude-code:python-review`** — PEP 8, Pythonic idioms, type hints, security, performance. Justification: lots of new Python code in scoring engine, signal math.
3. **`everything-claude-code:database-reviewer`** — schema, migrations, index strategy, query performance, RLS. Justification: significant schema changes, new tables, GENERATED columns, view rewrite.

If any audit flags an issue: fix → re-run that audit only → proceed only when clean. No push with pending flags.

Final audit on PR3 adds **`supply-chain-risk-auditor@trailofbits-skills`** because we will have accumulated new Python dependencies (`httpx`, `scipy` for Benford chi-square, potentially `eth_utils` for address validation).

## 13. Rollback Strategy

- **Backend:** Railway keeps past releases; rollback = redeploy previous SHA.
- **Frontend:** Vercel keeps past deployments; rollback via "promote previous production".
- **Database forward-compatible:** new columns added first, legacy columns dropped only after 48h stable. If analyzer fails post-deploy, `UPDATE scanner_scores SET classification='UNCERTAIN' WHERE scored_at > '<deploy_ts>'` and rollback code. Worst case: `TRUNCATE scanner_scores` and let analyzer rebuild from previous-version image.

## 14. Monitoring Queries (post-deploy sanity checks)

```sql
-- Distribution check
SELECT classification, COUNT(*), ROUND(AVG(p_bot)::numeric, 3) AS avg_p
FROM scanner_agent_latest_scores
WHERE p_bot IS NOT NULL
GROUP BY classification
ORDER BY avg_p;

-- Classification flips in the last hour
WITH last_hour AS (
  SELECT agent_id, classification, scored_at,
         LAG(classification) OVER (PARTITION BY agent_id ORDER BY scored_at) AS prev_cls
  FROM scanner_scores
  WHERE scored_at > NOW() - INTERVAL '1 hour'
)
SELECT a.name, l.prev_cls, l.classification, l.scored_at
FROM last_hour l JOIN scanner_agents a ON a.id = l.agent_id
WHERE l.prev_cls IS NOT NULL AND l.prev_cls != l.classification
ORDER BY l.scored_at DESC LIMIT 50;

-- Hard gate frequency
SELECT unnest(hard_gates_hit) AS gate, COUNT(*)
FROM scanner_agent_latest_scores
WHERE hard_gates_hit IS NOT NULL AND array_length(hard_gates_hit, 1) > 0
GROUP BY gate ORDER BY 2 DESC;

-- Evidence log depth (sanity: each agent should have many signal rows)
SELECT jsonb_array_length(evidence_log) AS signals, COUNT(*)
FROM scanner_agent_latest_scores
GROUP BY 1 ORDER BY 1;
```

## 15. Explicit Non-Goals

- Machine learning of any kind (Bayesian evidence + calibration is sufficient and explainable).
- Telegram/Slack/Discord notifications (removed entirely in PR1).
- Public labels page (labels managed via Supabase MCP directly).
- Authentication on the dashboard (remains public).
- Real-time push alerts on classification changes.
- Historical population-level trend charts (per-agent history only).
- A/B testing framework (LR versioning is sufficient).

## 16. Open Items Tracked for Later

- Etherscan V2 API key registration (blocker for PR3).
- Hyperliquid candle fetch rate-limit behavior (to be validated empirically in PR2).
- Possible future R1 (timing correlation across agents) — needs population-scale SQL.
- Possible future ML layer once we have 200+ labels.

---

**End of design document.** Implementation planning continues in `writing-plans` skill.
