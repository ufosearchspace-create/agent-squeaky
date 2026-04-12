# Agent Squeaky

Bayesian bot-vs-human classifier for the [DegenClaw](https://degen.virtuals.io) AI trading agent arena on Virtuals/Hyperliquid.

**Live dashboard:** [squeaky.ufxproject.com](https://squeaky.ufxproject.com)

## What it does

Agent Squeaky examines every competitor in the DegenClaw arena and answers: *is this a bot, a human, or a hybrid?*

It starts with a strong prior that every agent is a bot (P(BOT) = 0.95) and only shifts toward HUMAN when multiple independent signals converge on human-like behaviour. The system uses **Bayesian evidence accumulation** with log2-likelihood-ratio bits across 34 signals in seven families:

| Family | Signals | What they measure |
|---|---|---|
| Temporal | T1–T8 | Sleep gaps, circadian stability, weekend drops, burst patterns, ms entropy |
| Structural | S1–S9 | Round sizes, decimal precision, Benford compliance, coin diversity, leverage |
| Behavioural | B1–B5 | Hold-time variance, bimodality, win/loss asymmetry, concurrent positions |
| Reaction | B4/B4b | Price-spike reaction lag from 5-minute Hyperliquid candles |
| On-chain | M1–M6 | Owner wallet age, multi-chain presence, tx count, throwaway detection |
| Relative | M5 | Behavioural fingerprint divergence within owner clusters |
| Psychology | B6–B10, S8–S9, T9 | Disposition effect, loss-chase, hot-hand tempo, tilt spikes, round-number exits, anchor exits, gap entropy, intraday emotion shape |

### Hard gates

Some observations override the Bayesian posterior directly:

- **HG1** Manual BOT/HUMAN label — wins outright
- **HG2** Persistent 24/7 operation (median 22+ active hours, 10+ days) — forces BOT
- **HG3** Coordinated farm (4+ agents, 3+ matching fingerprints) — forces BOT
- **HG4** Seasoned owner wallet (365+ days, 3+ chains, 100+ txs) — caps at UNCERTAIN
- **HG5** Throwaway farm (wallet < 30 days + sibling cluster) — forces BOT

### FARM detection

Agents whose owner runs a sybil farm are flagged with a **FARM** marker. This is separate from the bot/human classification — it identifies operators who compete as multiple entities to inflate their chance of landing in the prize pool.

### HUMAN_ASSISTED detection

Eight psychology signals grounded in academic trading-psychology literature detect **hybrid agents** — bots with occasional human intervention. When three or more psychology signals converge on human-leaning evidence despite a bot-leaning posterior, the agent is flagged as **HUMAN-ASSISTED**.

Academic references: Shefrin & Statman (1985), Kahneman & Tversky (1979), Gilovich et al. (1985), Lo/Repin/Steenbarger (2005), Harris (1991), Tversky & Kahneman (1974), Czeisler et al. (circadian), Lopez de Prado (2018).

## Architecture

```
Railway (Python 3.11+)          Supabase (Postgres)          Vercel (Next.js)
  collector.py ──────────────▶ scanner_agents/trades
  candle_fetcher.py ─────────▶ scanner_candles
  onchain_enricher.py ───────▶ scanner_onchain
  analyzer.py ───────────────▶ scanner_scores ──────────────▶ dashboard
    scoring_engine/                                            page.tsx
      signals/ (34 signals)                                    agent/[id]/page.tsx
      bayesian.py
      classifier.py
      gates.py
```

- **Backend**: Python worker on Railway with APScheduler (no web server). Collects trades every 30 minutes, enriches with Hyperliquid candles and Basescan on-chain data, scores every agent through the Bayesian pipeline.
- **Database**: Supabase Postgres with RLS-enabled public-read tables. Signal LRs stored in `scanner_signal_lrs` for data-driven calibration without code deploys.
- **Frontend**: Next.js + React + Tailwind with a steampunk warm-copper aesthetic. Reads directly from Supabase via the public anon key.

## Running locally

### Backend

```bash
cd backend
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_SERVICE_KEY, DGCLAW_API_KEY

pip install -r requirements.txt
python -m pytest          # 204 tests
python main.py            # starts scheduler
```

### Frontend

```bash
cd frontend
cp .env.example .env.local
# Fill in NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY

npm install
npm run dev
```

## Tests

```bash
cd backend && python -m pytest -q
# 204 passed
```

Test suite covers all 34 signals, 5 hard gates, Bayesian math, classifier thresholds, HUMAN_ASSISTED evaluator, and synthetic fixture generators for bot/human/hybrid trade patterns.

## License

[GNU Affero General Public License v3.0](LICENSE) — you may use, modify, and distribute this software, but if you host a modified version as a service, you must make your source code available under the same license.

## Built by

[UFX Project](https://www.ufxproject.com) — also competing in the DegenClaw arena with our own AI trading agent.
