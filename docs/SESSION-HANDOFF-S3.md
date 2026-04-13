# Agent Squeaky — Session Handoff (Season 3 Transition)

**Date:** 2026-04-13
**For:** Next Claude Code context window
**Author:** Previous session (PR1→PR2→PR3→PR4 + public release)

---

## 1. What is Agent Squeaky

Bayesian bot/human classifier for the [DegenClaw](https://degen.virtuals.io) AI trading competition on Virtuals Protocol / Hyperliquid. Live dashboard at https://squeaky.ufxproject.com (alt: https://agent-squeaky.vercel.app).

**Owner:** Sasa Pejic (@ufxproject). Croatian. Prefers Croatian communication. Follows strict 5-step discipline: razumi→prouči→potvrdi→implementiraj→audit. Demands CEK audit after each major segment.

**Stack:**
- Backend: Python 3.11+ on Railway (worker, no web server). APScheduler.
- DB: Supabase Postgres (project `zgoznvyeicbvqqoghmug`). RLS public-read.
- Frontend: Next.js 16.2.1 + React 19 + Tailwind 4 in `frontend/` (gitignored, deploys via `npx vercel@latest --prod --yes`).
- Steampunk warm-copper visual theme (Playfair Display serif, brass palette).
- License: AGPL-3.0 (repo is now public).

**GitHub:** https://github.com/ufosearchspace-create/agent-squeaky

---

## 2. Architecture Overview

```
Railway (Python)                 Supabase (Postgres)              Vercel (Next.js)
  collector.py       ─30m──▶   scanner_agents / scanner_trades
  candle_fetcher.py  ─30m──▶   scanner_candles
  onchain_enricher.py ─24h──▶  scanner_onchain
  analyzer.py        ─30m──▶   scanner_scores ──────────────────▶ dashboard
    scoring_engine/                                                page.tsx
      signals/                                                     agent/[id]/page.tsx
        temporal.py      (T1-T8)
        structural.py    (S1-S7)
        behavioral.py    (B1-B5)
        reaction.py      (B4/B4b)
        onchain.py       (M1-M6)
        meta.py          (M5)
        psychology.py    (B6-B10, S8-S9, T9)  ← PR4
      bayesian.py        (posterior calculation)
      calibration.py     (LR cache from scanner_signal_lrs)
      classifier.py      (classify + evaluate_human_assisted)
      gates.py           (HG1-HG5 hard gates)
```

**Scheduler order (main.py):**
1. `collector.run()` at +10s, then every 30m
2. `candle_fetcher.run()` at +10m, then every 30m
3. `analyzer.run()` at +20m, then every 30m
4. `onchain_enricher.run()` daily at 02:00 UTC

---

## 3. Signal Inventory (34 signals)

### Temporal (T1-T8) — `signals/temporal.py`
| Signal | What it measures | Min data |
|--------|-----------------|----------|
| T1 | Per-day sleep gap (hours) | 5 days |
| T2 | Sleep window stability (circular stddev) | T1 human |
| T3 | Weekend/weekday trade ratio | 7 days both |
| T4 | Daily volume coefficient of variation | 7 days |
| T5 | Dead days (zero-trade days) | 7 days |
| T6 | Intraday burst score (5-min clusters) | 20 trades |
| T7 | Per-day inter-trade interval CV | 7 days |
| T8 | Millisecond entropy of timestamps | 50 trades |

### Structural (S1-S7) — `signals/structural.py`
| Signal | What it measures | Min data |
|--------|-----------------|----------|
| S1 | Round position size percentage | 20 trades |
| S2 | Size decimal precision (avg digits) | 20 trades |
| S3 | Benford's Law compliance | 50 trades |
| S4 | Coin diversity (unique coins) | 10 trades |
| S5 | Fixed ladder pattern detection | 30 trades |
| S6 | Identical-size repetition frequency | 20 trades |
| S7 | Leverage variance | 20 trades |

### Behavioural (B1-B5) — `signals/behavioral.py`
| Signal | What it measures | Min data |
|--------|-----------------|----------|
| B1 | Hold-time variance (log CV + bimodal) | 20 trades |
| B2 | Hold-time median | 10 trades |
| B3 | Win/loss hold asymmetry | 10W/10L |
| B5 | Concurrent open positions | 10 trades |

### Reaction (B4/B4b) — `signals/reaction.py`
| Signal | What it measures | Min data |
|--------|-----------------|----------|
| B4 | Price-spike reaction lag (5m candles) | 10 reactions |
| B4b | Zero-bucket reaction fraction | 10 reactions |

### On-chain (M1-M6) — `signals/onchain.py`
| Signal | What it measures | Min data |
|--------|-----------------|----------|
| M1 | Owner wallet age (days) | onchain row |
| M2 | Shared funder detection | owner cluster |
| M3 | Multi-chain presence | onchain row |
| M6 | Wallet age bucket | onchain row |

### Relative (M5) — `signals/meta.py`
| Signal | What it measures | Min data |
|--------|-----------------|----------|
| M5 | Fingerprint divergence from owner cluster | 2+ siblings |

### Psychology (B6-B10, S8-S9, T9) — `signals/psychology.py` ← PR4
| Signal | What it measures | Academic source | Min data |
|--------|-----------------|----------------|----------|
| B6 | Disposition effect (loss/win hold ratio) | Shefrin & Statman 1985 | 5W/5L |
| B7 | Loss-chase sizing (Pearson r) | Kahneman & Tversky 1979 | 30 trades |
| B8 | Hot-hand tempo (pace vs win rate) | Gilovich 1985 | 30 trades |
| B9 | Tilt spike (post-big-loss rate ratio) | Lo/Repin/Steenbarger 2005 | 30 trades + 3 big losses |
| S8 | Round PnL exits (fraction near round) | Harris 1991 | 20 trades |
| S9 | Anchor exits (round % returns) | Tversky & Kahneman 1974 | 20 trades |
| T9 | Gap entropy (Shannon, gap start hours) | Czeisler et al. | 30 trades + 7d span |
| B10 | Intraday emotion shape (CV + U-ratio) | Lopez de Prado 2018 | 50 trades |

---

## 4. Hard Gates (HG1-HG5) — `gates.py`

| Gate | Trigger | Effect |
|------|---------|--------|
| HG1 | Manual BOT/HUMAN label in scanner_labels | Forces that class |
| HG2 | Median 22+ active hours/day, 10+ days | Forces BOT |
| HG3 | Owner ≥4 agents, ≥3 matching fingerprint | Forces BOT (farm) |
| HG4 | Owner wallet ≥365d, ≥3 chains, ≥100 txs | Caps at UNCERTAIN |
| HG5 | Owner wallet <30d + ≥2 siblings + ≥20 trades | Forces BOT (throwaway) |

**Precedence:** HG1 > HG2 > HG3 > HG5 > HG4 > natural class.

---

## 5. Classification + Flags

### Base classification (classifier.py:classify)
| Class | P(bot) range |
|-------|-------------|
| BOT | ≥ 97% |
| LIKELY_BOT | 85–97% |
| UNCERTAIN | 60–85% |
| LIKELY_HUMAN | 30–60% |
| HUMAN | < 30% |

**Prior:** P(BOT) = 0.95 → log-odds = 4.25 bits

### Parallel flags (not classes — coexist with base classification)

**FARM** — fires if hard_gates_hit contains `gate:farm_*` or `gate:throwaway_farm`. Frontend: amber plaque, `plaque-FARM` CSS class.

**HUMAN_ASSISTED** — fires when:
- `p_bot >= 0.60` (CEK R1 fix, was 0.75)
- `trade_count >= 30`
- `≥ 3` psychology signals have `log_lr_bits <= -0.5` (CEK R6 fix, was 2)
- `gate:labeled` NOT in hard_gates_hit
- All other gates coexist

Frontend: violet plaque, `plaque-HA` CSS class.

---

## 6. Database Schema (key tables)

### scanner_agents
```
id TEXT PK, name, wallet_address, agent_address, token_address,
owner_wallet (indexed), trade_count, win_count, loss_count,
total_pnl, win_rate, last_updated
```

### scanner_trades
```
id SERIAL PK, agent_id FK, dgc_trade_id UNIQUE, opened_at_ms,
closed_at_ms, hold_time_s, coin, direction, entry_price, exit_price,
position_size, leverage, closed_pnl, created_at
```

### scanner_scores
```
id SERIAL PK, agent_id FK, scored_at, prior_log_odds, posterior_log_odds,
p_bot, evidence_log JSONB, hard_gates_hit TEXT[], classification,
human_assisted BOOLEAN (PR4), lr_version, trade_count_at_scoring, flags TEXT[]
```

### scanner_candles
```
(coin, interval, ts_ms) PK, open, high, low, close, volume, fetched_at
```

### scanner_onchain
```
owner_wallet TEXT PK, base_first_tx_date, age_days, total_tx_count,
chains_active, balance_usd, address_kind, last_refreshed_at, source
```

### scanner_labels
```
agent_id TEXT PK FK, label ('BOT'|'HUMAN'|'SUSPICIOUS'|'UNSURE'),
reason, labeled_by, labeled_at
```

### scanner_signal_lrs
```
(signal_name, version) PK, lr_log_bot, lr_log_human,
thresholds JSONB {"states": {state: bits}, "cutoffs": {...}},
description, calibrated_by, active BOOLEAN
```

### scanner_agent_latest_scores (VIEW)
Joins scanner_agents + latest scanner_scores + scanner_onchain. Exposes: all agent fields + scoring fields + human_assisted + owner_agents_count + owner_age/activity/chains.

---

## 7. Frontend Structure

```
frontend/src/
  app/
    globals.css          ← steampunk palette, plaque classes, gauge bars
    layout.tsx           ← Playfair Display font
    page.tsx             ← main dashboard (table, filters, methodology)
    agent/[id]/page.tsx  ← agent dossier (waterfall, charts, psychology)
  lib/
    supabase.ts          ← singleton client
    types.ts             ← AgentScore, Trade, EvidenceEntry, etc.
    farm.ts              ← isFarmAgent, groupFarmsByOwner
    psychology.ts        ← isHumanAssisted, PSYCHOLOGY_EXPLAIN mapping
```

**Key CSS classes:** `.brass-plate`, `.brass-frame`, `.engrave`, `.plaque`, `.plaque-BOT/LIKELY_BOT/UNCERTAIN/LIKELY_HUMAN/HUMAN/FARM/HA/gate`, `.brass-pill`, `.gauge-track`, `.agent-name`, `.farm-alert`, `.ha-alert`

---

## 8. PR History

| PR | What | Tests |
|----|------|-------|
| PR1 | Bayesian scoring redesign: 20 signals, HG1/HG2/HG3, classifier, prior 0.95 | 157 |
| PR2 | Hyperliquid 5m candle scraping + B4/B4b reaction signals | ~165 |
| PR3 | Basescan on-chain enrichment + M1/M2/M3/M6 + HG4/HG5 | ~170 |
| PR4 | Psychology signals (B6-B10, S8-S9, T9) + HUMAN_ASSISTED flag + steampunk redesign + FARM detection | 204 |

---

## 9. Season 3 — What Changed in DegenClaw

**Scraped from https://degen.virtuals.io/docs on 2026-04-13:**

### New features in S3
1. **HIP-3 assets** — tokenized equities (NVDA, TSLA, AAPL, META, AMZN, GOOGL, etc.), indices (SP500, XYZ100), commodities (GOLD, SILVER, CRUDE OIL, COPPER), currencies (EUR, JPY, DXY). These use `xyz:` prefix in trade commands.
2. **AI Council** — 3 independent AI models (GPT-5.4, Gemini 3.1, Opus 4.6) select top 10 agents weekly. Each receives complete Hyperliquid on-chain history + forum posts. $100K USDC pot mirrors winners from Tuesday 8am SGT.
3. **7-day seasons** — Tuesday 8am SGT → next Tuesday 8am SGT. All positions close at season end.
4. **Token burn mechanics** — 50% of realized profits from copy-trading → buyback & burn of that agent's token. 50% rolls to next season. Losses absorbed by Virtuals (not socialized).
5. **Forum rationale posts** — optional but factored into council evaluation.

### Impact on Squeaky
1. **HIP-3 coin names** — trades may now show coins like `xyz:GOLD`, `xyz:NVDA`. Our collector stores `coin` from API `token` field. Need to verify these flow through correctly. Structural signals (S4 coin diversity) and reaction signals (B4 candle correlation) need to handle `xyz:` prefixed coins.
2. **Candle fetcher** — `candle_fetcher.py` scrapes Hyperliquid 5m candles per coin. HIP-3 assets may or may not be available on Hyperliquid's candle API. Need investigation.
3. **Agent roster change** — some S1/S2 agents may not be in S3. New agents may have appeared. Our collector fetches the leaderboard every 30 min, so new agents are picked up automatically. Old agents remain in DB with stale data.
4. **Potential API changes** — the DegenClaw API endpoints (`/api/leaderboard`, `/api/agents/{id}/trades`) may have changed between seasons. Need to verify collector still works.

### TODO for Season 3 adaptation
- [ ] Verify collector still fetches agents and trades correctly from the DegenClaw API
- [ ] Check if HIP-3 coins (xyz: prefix) appear in collected trades
- [ ] Verify candle_fetcher handles xyz: coins or gracefully skips them
- [ ] Consider: should we reset/archive S1/S2 scores and start fresh for S3?
- [ ] Consider: should inactive agents (no new trades in 7+ days) be visually flagged?
- [ ] Verify HUMAN_ASSISTED flag is populating after Railway deploy (first rescore should have happened by now)
- [ ] Check if the DegenClaw API now returns different field names or structure

---

## 10. Current State (as of 2026-04-13)

### What's deployed and working
- **Backend on Railway:** PR4 code pushed (`de2081f`), should be auto-deployed. 34 signals active.
- **DB migrations:** 013 (psychology LRs) + 014 (human_assisted column + view) applied via Supabase MCP.
- **Frontend on Vercel:** Steampunk theme + FARM + HUMAN_ASSISTED plaques live.
- **GitHub:** Public repo with AGPL-3.0, README, prompt file deleted, infra refs scrubbed.

### What needs verification
1. **Has Railway deployed?** Check Railway dashboard or logs.
2. **Has analyzer rescored with psychology signals?** Query: `SELECT COUNT(*) FROM scanner_scores WHERE human_assisted = true`
3. **Are new S3 agents appearing?** Query: `SELECT COUNT(*) FROM scanner_agents` and compare to last known count (~225).
4. **Do HIP-3 trades flow correctly?** Query: `SELECT DISTINCT coin FROM scanner_trades WHERE coin LIKE 'xyz:%'`

### Distribution snapshot before PR4 (2026-04-11)
| Classification | Count | Avg P(bot) |
|---|---|---|
| BOT | 133 | 99.3% |
| LIKELY_BOT | 57 | 93.8% |
| UNCERTAIN | 26 | 80.4% |
| LIKELY_HUMAN | 5 | 49.2% |
| HUMAN | 4 | 14.2% |

**Labels:** 14 BOT, 1 HUMAN, 6 SUSPICIOUS. 15/15 accuracy on labeled samples.
**Farms:** 0 HG3 coordinated, 3 HG5 throwaway. 8 HG4 ceiling.

---

## 11. CEK Audit Results (PR4 Backend)

**Overall: 8.17/10.** Requirements 9.2, Architect 7.5, Quality 7.8.

### Critical fixes APPLIED
- **R1** — `HUMAN_ASSISTED_MIN_P_BOT` 0.75 → 0.60 (self-cancellation prevention)
- **R6** — `HUMAN_ASSISTED_MIN_SIGNALS` 2 → 3 (Freqtrade DCA false-positive prevention)
- **C1** — B9 tilt_spike O(n²) → O(n log n) via bisect

### HIGH items DEFERRED (not blocking, follow-up)
- H2: Magic numbers duplicated Python ↔ SQL seed
- H1: Duplicated win/loss hold extraction (B3 vs B6)
- R2/R3: B7/B8 overlapping-window Pearson autocorrelation
- R5: T9 gap entropy double-counted human states
- R7: Migration 014 DROP+CREATE VIEW (should be CREATE OR REPLACE)
- No ha_signals persistence column
- B10/T9 test assertions are permissive

---

## 12. Key Files Quick Reference

| Purpose | File |
|---------|------|
| Scheduler entry point | `backend/main.py` |
| Trade collection | `backend/collector.py` |
| Candle scraping | `backend/candle_fetcher.py` |
| On-chain enrichment | `backend/onchain_enricher.py` |
| Signal dispatch + scoring | `backend/analyzer.py` |
| Signal contracts | `backend/scoring_engine/base.py` |
| Bayesian math | `backend/scoring_engine/bayesian.py` |
| LR cache | `backend/scoring_engine/calibration.py` |
| Classification + HA | `backend/scoring_engine/classifier.py` |
| Hard gates | `backend/scoring_engine/gates.py` |
| Psychology signals | `backend/scoring_engine/signals/psychology.py` |
| Config (env vars) | `backend/config.py` |
| DB client | `backend/db.py` |
| Test fixtures | `backend/tests/fixtures_synth.py` |
| Frontend types | `frontend/src/lib/types.ts` |
| Farm helpers | `frontend/src/lib/farm.ts` |
| Psychology helpers | `frontend/src/lib/psychology.ts` |
| Main dashboard | `frontend/src/app/page.tsx` |
| Agent dossier | `frontend/src/app/agent/[id]/page.tsx` |
| Steampunk styles | `frontend/src/app/globals.css` |

---

## 13. Memory Files (for Claude Code auto-memory)

Located at `C:\Users\pejic\.claude\projects\d--Agent-Squeaky\memory\`:

| File | Content |
|------|---------|
| MEMORY.md | Index of all memory files |
| project_squeaky.md | Project overview |
| feedback_scoring.md | Scoring philosophy decisions |
| reference_squeaky_infra.md | Infrastructure URLs and IDs |
| feedback_workflow.md | 5-step discipline rule |
| reference_toolkit.md | Curated tool references |
| project_pr4_psychology.md | PR4 full progress log with CEK results |
| project_public_github.md | Public release decisions |

---

## 14. User Preferences (from memory)

- **Language:** Croatian for conversation, English for code/docs
- **Workflow:** razumi→prouči→potvrdi→implementiraj→audit. CEK audit after each major segment. Never skip.
- **Scoring philosophy:** No frequency penalty. D2+D3 override for definitive bots. Conservative — false negatives preferred over false positives for investor-facing flags.
- **Code style:** Clean, concise. Comment WHY not WHAT. No over-engineering. Security audit before every push.
- **Deploy:** Frontend via `npx vercel@latest --prod --yes` from `frontend/`. Backend auto-deploys on git push to master via Railway.
- **Supabase project ID:** `zgoznvyeicbvqqoghmug`

---

## 15. Immediate Priority for Next Session

1. **Verify S3 API compatibility** — is collector.py still fetching agents/trades?
2. **Check HIP-3 coin handling** — do `xyz:GOLD` etc. trades show up?
3. **Verify HUMAN_ASSISTED rescore** — did Railway deploy and analyzer run?
4. **Handle inactive agents** — flag or filter agents not trading in S3
5. **Potential: Season-aware scoring** — reset/separate S3 scores from S1/S2 history?
6. **Set GitHub repo to Public** — user must do manually in Settings > Danger Zone

---

*Generated 2026-04-13 by the session that built PR1-PR4 and prepared public release.*
