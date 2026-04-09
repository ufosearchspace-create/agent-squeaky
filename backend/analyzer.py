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

from config import TABLE_AGENTS, TABLE_CANDLES, TABLE_LABELS, TABLE_SCORES, TABLE_TRADES
from db import get_client
from scoring_engine import calibration
from scoring_engine.base import SignalContext
from scoring_engine.bayesian import PRIOR_LOG_ODDS_BITS, posterior
from scoring_engine.classifier import classify
from scoring_engine.gates import apply_hard_gates
from scoring_engine.signals.behavioral import ALL_BEHAVIORAL_SIGNALS
from scoring_engine.signals.meta import ALL_META_SIGNALS
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


def _load_candles_for_trades(
    trades: list[dict],
    lookback_days: int = 15,
) -> dict[str, list[dict]]:
    """Fetch 5m candles for every distinct coin in ``trades``.

    Returns ``{coin: [candle, ...]}`` with the columns the reaction
    signals need. The query uses ``.in_("coin", coins)`` so the whole
    agent payload is one round-trip regardless of how many coins they
    trade. The candle field carries ``coin`` so we can bucket client-side.

    Lookback is capped at 15 days because B4 only cares about
    spike-to-trade proximity within minutes; older candles are dead weight.
    The limit is sized generously enough to hold ``coins_per_agent ×
    candles_per_coin`` without tripping Supabase's default 1000-row cap
    silently.
    """
    coins = sorted({t["coin"] for t in trades if t.get("coin")})
    if not coins:
        return {}
    sb = get_client()
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000
    # 288 candles/day × lookback × #coins, plus generous headroom so we
    # never silently truncate. PostgREST hard-caps at 10000 by default;
    # we ask for the full set.
    max_rows = len(coins) * lookback_days * 300 + 1000
    rows = (
        sb.table(TABLE_CANDLES)
        .select("coin,ts_ms,open,high,low,close,volume")
        .in_("coin", coins)
        .eq("interval", "5m")
        .gte("ts_ms", since_ms)
        .order("ts_ms")
        .limit(max_rows)
        .execute()
        .data
        or []
    )
    out: dict[str, list[dict]] = {}
    for row in rows:
        coin = row.get("coin")
        if not coin:
            continue
        out.setdefault(coin, []).append(row)
    return out


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


def score_agent(agent: dict) -> dict | None:
    """Score one agent. Returns the inserted scanner_scores row or None."""
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

    ctx = SignalContext(
        agent=agent,
        trades=trades,
        candles=_load_candles_for_trades(trades),
        onchain=None,  # populated in PR3
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
        onchain=None,
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

    agents = sb.table(TABLE_AGENTS).select("*").execute().data or []
    scored = 0
    for agent in agents:
        try:
            if score_agent(agent):
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
