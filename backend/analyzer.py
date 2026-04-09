"""Analyzer: Bayesian scoring of all eligible agents every 30 minutes.

Loads each agent plus its trades, runs every registered signal module,
sums the log-LR contributions via scoring_engine.bayesian.posterior,
applies hard gates, and writes one scanner_scores row per agent with
the full evidence log in jsonb.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from config import (
    TABLE_AGENTS,
    TABLE_CANDLES,
    TABLE_LABELS,
    TABLE_ONCHAIN,
    TABLE_SCORES,
    TABLE_TRADES,
)
from db import get_client
from scoring_engine import calibration
from scoring_engine.base import SignalContext
from scoring_engine.bayesian import PRIOR_LOG_ODDS_BITS, posterior
from scoring_engine.classifier import classify
from scoring_engine.gates import apply_hard_gates
from scoring_engine.signals.behavioral import ALL_BEHAVIORAL_SIGNALS
from scoring_engine.signals.meta import ALL_META_SIGNALS
from scoring_engine.signals.onchain import ALL_ONCHAIN_SIGNALS
from scoring_engine.signals.reaction import ALL_REACTION_SIGNALS
from scoring_engine.signals.structural import ALL_STRUCTURAL_SIGNALS
from scoring_engine.signals.temporal import ALL_TEMPORAL_SIGNALS

logger = logging.getLogger(__name__)

MIN_TRADES = 3

# Order matters only for display and determinism in the evidence log.
ALL_SIGNALS = (
    ALL_TEMPORAL_SIGNALS
    + ALL_STRUCTURAL_SIGNALS
    + ALL_BEHAVIORAL_SIGNALS
    + ALL_REACTION_SIGNALS
    + ALL_ONCHAIN_SIGNALS
    + ALL_META_SIGNALS
)


def _load_label(agent_id: str) -> str | None:
    sb = get_client()
    rows = (
        sb.table(TABLE_LABELS)
        .select("label")
        .eq("agent_id", agent_id)
        .execute()
        .data
        or []
    )
    if rows:
        return rows[0].get("label")
    return None


#: Maximum candles we keep in memory per coin. 288 candles/day × 15 days
#: = 4320; we add a small headroom so the post-ts filter never trims too
#: aggressively.
_CANDLES_PER_COIN_CAP = 4500


def _load_all_candles(lookback_days: int = 15) -> dict[str, list[dict]]:
    """Fetch all 5m candles for the last ``lookback_days`` in one pass.

    The analyzer calls this once per ``run()`` and keeps the result in
    memory for every subsequent ``score_agent`` call. This avoids the
    old N+1 per-agent pattern that would otherwise issue 275 sequential
    ``.in_("coin", …)`` queries, each returning up to ~80k rows.

    We fetch the rows coin-by-coin in ascending ``ts_ms`` order so that
    each individual query stays below Supabase's default row cap
    (1000 rows pre-configured, 10000 hard ceiling). _CANDLES_PER_COIN_CAP
    is comfortably below both. Fetching the dataset in slices keeps the
    payload on each HTTP response small enough to avoid timeouts.
    """
    sb = get_client()
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000

    # Discover which coins actually have candles in the window. One cheap
    # query against scanner_trades to get the working set.
    recent_coins_rows = (
        sb.table(TABLE_TRADES)
        .select("coin")
        .gte("closed_at_ms", since_ms)
        .execute()
        .data
        or []
    )
    coins = sorted({r["coin"] for r in recent_coins_rows if r.get("coin")})
    if not coins:
        return {}

    out: dict[str, list[dict]] = {}
    for coin in coins:
        rows = (
            sb.table(TABLE_CANDLES)
            .select("ts_ms,open,high,low,close,volume")
            .eq("coin", coin)
            .eq("interval", "5m")
            .gte("ts_ms", since_ms)
            .order("ts_ms")
            .limit(_CANDLES_PER_COIN_CAP)
            .execute()
            .data
            or []
        )
        if rows:
            out[coin] = rows
    logger.info(
        "Loaded %d candle rows across %d coins for analyzer cycle",
        sum(len(v) for v in out.values()),
        len(out),
    )
    return out


def _filter_candles_for_trades(
    all_candles: dict[str, list[dict]],
    trades: list[dict],
) -> dict[str, list[dict]]:
    """Project the shared candle cache onto the coins a single agent traded."""
    if not all_candles:
        return {}
    coins = {t["coin"] for t in trades if t.get("coin")}
    return {coin: all_candles[coin] for coin in coins if coin in all_candles}


def _load_all_onchain() -> dict[str, dict]:
    """Fetch the entire scanner_onchain table into an in-memory index.

    One query per analyzer cycle. The table holds at most a few hundred
    rows (one per distinct owner_wallet) so this is trivial compared to
    the 275 per-agent lookups the naive pattern would do. Returns
    ``{owner_wallet_lowercase: onchain_row}``.
    """
    sb = get_client()
    rows = (
        sb.table(TABLE_ONCHAIN)
        .select("*")
        .execute()
        .data
        or []
    )
    out: dict[str, dict] = {}
    for row in rows:
        owner = row.get("owner_wallet")
        if not owner:
            continue
        out[owner.lower()] = row
    return out


def _onchain_for_agent(
    all_onchain: dict[str, dict],
    agent: dict,
) -> dict | None:
    """Look up the onchain row for an agent's owner_wallet (case-insensitive)."""
    owner = agent.get("owner_wallet")
    if not owner:
        return None
    return all_onchain.get(owner.lower())


def _load_owner_cluster(agent: dict) -> list[dict]:
    owner = agent.get("owner_wallet")
    if not owner:
        return []
    sb = get_client()
    rows = (
        sb.table(TABLE_AGENTS)
        .select("id,name,owner_wallet")
        .eq("owner_wallet", owner)
        .execute()
        .data
        or []
    )
    # M5 wants fingerprints on siblings but a fully loaded cluster requires
    # a second scoring pass. We attach minimal info here; M5 falls back to
    # None (no evidence) until fingerprint wiring lands in a follow-up.
    return rows


def score_agent(
    agent: dict,
    all_candles: dict[str, list[dict]] | None = None,
    all_onchain: dict[str, dict] | None = None,
) -> dict | None:
    """Score one agent. Returns the inserted scanner_scores row or None.

    ``all_candles`` and ``all_onchain`` are shared per-run caches
    produced by ``_load_all_candles`` and ``_load_all_onchain``. When
    either is absent (e.g. direct unit tests) we fall back to a lazy
    load of the slice this agent actually needs.
    """
    sb = get_client()
    trades = (
        sb.table(TABLE_TRADES)
        .select("*")
        .eq("agent_id", agent["id"])
        .order("closed_at_ms")
        .execute()
        .data
        or []
    )
    if len(trades) < MIN_TRADES:
        logger.info(
            "Agent %s (%s): only %d trades, skipping",
            agent.get("id"),
            agent.get("name"),
            len(trades),
        )
        return None

    if all_candles is None:
        all_candles = _load_all_candles()
    agent_candles = _filter_candles_for_trades(all_candles, trades)

    if all_onchain is None:
        all_onchain = _load_all_onchain()
    agent_onchain = _onchain_for_agent(all_onchain, agent)

    ctx = SignalContext(
        agent=agent,
        trades=trades,
        candles=agent_candles,
        onchain=agent_onchain,
        now_ms=int(time.time() * 1000),
        owner_cluster=_load_owner_cluster(agent),
    )

    evidence = [sig(ctx) for sig in ALL_SIGNALS]
    p_bot, post_log_odds, evidence_log = posterior(evidence)
    natural = classify(p_bot)

    label = _load_label(agent["id"])
    final_class, gates_hit = apply_hard_gates(
        agent=agent,
        trades=trades,
        owner_cluster=ctx.owner_cluster,
        onchain=agent_onchain,
        label=label,
        natural_class=natural,
    )

    row = {
        "agent_id": agent["id"],
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "prior_log_odds": PRIOR_LOG_ODDS_BITS,
        "posterior_log_odds": post_log_odds,
        "p_bot": p_bot,
        "evidence_log": evidence_log,
        "hard_gates_hit": gates_hit,
        "classification": final_class,
        "lr_version": calibration.current_version() or 1,
        "trade_count_at_scoring": len(trades),
        "flags": [],  # legacy text[] column still present in schema
    }
    sb.table(TABLE_SCORES).insert(row).execute()
    logger.info(
        "Agent %s (%s): p=%.3f cls=%s gates=%s evidence=%d",
        agent.get("id"),
        agent.get("name"),
        p_bot,
        final_class,
        gates_hit,
        len(evidence_log),
    )
    return row


def run() -> None:
    """Score all eligible agents. Called by APScheduler every 30 minutes."""
    logger.info("=== Analyzer started ===")
    start = time.time()
    calibration.reload_cache()
    sb = get_client()

    # Load candles and onchain enrichment ONCE per cycle so per-agent
    # scoring stays in-memory. Replaces the old N+1 pattern that was
    # hitting Supabase 275 times per cycle.
    try:
        all_candles = _load_all_candles()
    except Exception:
        logger.exception("Failed to load candle cache; B4 will be skipped")
        all_candles = {}
    try:
        all_onchain = _load_all_onchain()
        logger.info("Loaded %d onchain rows for analyzer cycle", len(all_onchain))
    except Exception:
        logger.exception("Failed to load onchain cache; M1/M2/M3/M6 will be skipped")
        all_onchain = {}

    agents = sb.table(TABLE_AGENTS).select("*").execute().data or []
    scored = 0
    for agent in agents:
        try:
            if score_agent(agent, all_candles=all_candles, all_onchain=all_onchain):
                scored += 1
        except Exception:
            logger.exception("Scoring failed for agent %s", agent.get("id"))

    elapsed = time.time() - start
    logger.info(
        "=== Analyzer done: %d/%d agents scored in %.1fs ===",
        scored,
        len(agents),
        elapsed,
    )
