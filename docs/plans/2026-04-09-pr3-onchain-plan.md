# PR3 Implementation Plan — Basescan On-Chain Enrichment + M1/M2/M3/M6 + HG4/HG5

**Branch:** `feat/phase-4-onchain`
**Goal:** Enrich owner wallets with Base on-chain metadata scraped from Basescan (HTML), introduce four new "meta" signals (M1 wallet age, M2 multi-chain presence, M3 activity score, M6 throwaway flag), and add two onchain-aware hard gates (HG4 ceiling, HG5 throwaway farm).

---

## Empirical findings from pre-plan research

1. Etherscan V2 free tier does NOT cover Base Mainnet. Base requires a paid tier. Ruled out.
2. Basescan HTML embeds everything we need in the `<meta name="Description">` tag plus a `First:` block. A single GET returns `Balance: $X.XX across N Chains`, `Transactions: N`, and the `First: ...ago` tooltip.
3. Parser validated on 5 real wallets. 4/5 returned full metadata. Monyet owner legitimately had 0 Base txs — a "dead EOA" owner that must be distinguished from "throwaway".
4. 157 distinct owners need enrichment. 185 agents carry owner_wallet, 90 have none.
5. Scraping budget: 157 / 7 days per week = ~22 owners/day * 2s polite delay = ~45s HTTP/day. Cloudflare risk low at this volume.

---

## Architecture

```
backend/onchain_enricher.py              (new)
backend/scoring_engine/signals/onchain.py (new)
backend/scoring_engine/gates.py          (add HG4, HG5)
backend/analyzer.py                      (load onchain cache once/run, wire to score_agent)
backend/main.py                          (add daily 24h onchain job)
backend/migrations/011_onchain_schema_tweak.sql
backend/migrations/012_seed_onchain_lrs.sql
backend/tests/test_onchain_parser.py
backend/tests/test_signals_onchain.py
backend/tests/test_gates.py              (extended)
```

## Parser spec

Regex anchors:

```python
META_DESC_RE = re.compile(
    r'<meta\s+name="Description"\s+content="'
    r'Address[^"]*?\|\s*Balance:\s*\$?([\d,.]+)'
    r'(?:\s*across\s+(\d+)\s+Chains?)?'
    r'[^"]*?\|\s*Transactions:\s*([\d,]+)',
    re.IGNORECASE,
)

FIRST_TX_RE = re.compile(
    r'First:.{0,600}?<span[^>]*>([^<]+ago)</span>',
    re.DOTALL | re.IGNORECASE,
)

ENS_RE = re.compile(r'Address\s*\(([^)]+)\)', re.IGNORECASE)
```

Relative age parser converts `1 yr 351 days ago`, `57 days ago`, `3 hrs ago` -> days.

## Rate limit contract

- 2.0 s polite delay between successful requests.
- User-Agent rotation across 3 realistic Chrome/Firefox strings.
- Retry policy: transport errors + 429/503 -> exp backoff (max 3 retries). 403 -> permanent skip for this run, count in a "blocked" counter; if >10 blocked in one cycle, abort and log ERROR.
- Freshness window: skip owners with `last_refreshed_at > NOW() - INTERVAL '7 days'`.

## Signals

### M1 owner_wallet_age
- medium_human: age >= 365 -> -1.5
- weak_human: age >= 180 -> -0.5
- neutral: 14 <= age < 180
- weak_bot: age < 14 AND total_tx_count > 0 -> +0.5
- None: age is None OR total_tx_count == 0 (dead EOA = insufficient data)

### M2 owner_multi_chain
- medium_human: chains_active >= 5 -> -1.5
- weak_human: chains_active >= 3 -> -0.5
- neutral: chains_active in (1, 2)
- None: chains_active is None OR total_tx_count == 0

### M3 owner_activity_score
- activity_score = log10(total_tx_count)
- medium_human: score >= 2.5 (>=316 txs) -> -1.5
- weak_human: score >= 1.5 (>=32 txs) -> -0.5
- neutral: score >= 0.5 (>=3 txs)
- None: total_tx_count == 0 or None

### M6 throwaway_owner_flag
- medium_bot: age < 14d AND 1 <= total_tx_count <= 10 -> +1.5
- weak_bot: age < 30d AND total_tx_count <= 20 -> +0.5
- neutral: everything else (including dead EOAs)

Dead EOA (`total_tx_count == 0`) is **neutral**, not bot. A legitimate pattern where the operator uses the AA proxy without touching the EOA directly.

## Hard gates

### HG4 onchain_human_ceiling
Conditions:
- age_days >= 365
- chains_active >= 3
- total_tx_count >= 100

Effect: if natural class is BOT or LIKELY_BOT -> cap at UNCERTAIN, tag `gate:onchain_human_ceiling`.
Does NOT force HUMAN — an experienced crypto operator can still run a bot. It only refuses to call them BOT with high confidence.

### HG5 throwaway_farm
Conditions:
- age_days < 30
- total_tx_count > 0 (dead EOA is not throwaway)
- owner_cluster_size >= 2
- agent trade count >= 20

Effect: force BOT with `gate:throwaway_farm` flag.

### Precedence in `apply_hard_gates`
1. HG1 label
2. HG2 persistent 24/7 -> BOT
3. HG3 coordinated farm -> BOT
4. HG5 throwaway_farm -> BOT (new)
5. HG4 onchain_human_ceiling -> UNCERTAIN (new, ceiling only)
6. Otherwise natural classification

## Test plan

- `test_onchain_parser.py` — parser on fixture HTML: full card, dead EOA, young throwaway, missing First block, Cloudflare 403 body, malformed meta
- `test_signals_onchain.py` — M1 / M2 / M3 / M6 state matrix on synthetic onchain dicts
- `test_gates.py` extended with HG4 + HG5 cases
- `test_analyzer_integration.py` extended to route `scanner_onchain` in the mock client
- Target: full suite green at ~125 tests after PR3

## Rollout

1. Implement, tests green locally
2. Apply migrations 011 + 012 via Supabase MCP
3. Audit quartet: `/cek:cek-critique` + python-review + database-reviewer + supply-chain-risk-auditor (final PR of the roadmap)
4. Push, PR, squash merge to master
5. Railway auto-deploys. Daily cron job fires on next 02:00 UTC tick
6. First enrichment cycle writes ~157 rows to scanner_onchain in ~5 min
7. Analyzer picks up onchain cache within 30 min, M1/M2/M3/M6 start appearing in evidence_log

## Non-goals

- No NFT count, distinct contracts count, ENS resolution, Funded By parse
- No Ethereum / Polygon / Arbitrum fallback
- No Playwright / JS-rendered fallback
- No feature flag — gates are always on once onchain rows exist
