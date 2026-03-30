"""
Analyzer: scores each agent on 6 dimensions (D1-D6) to classify as bot or human.
Runs every 6 hours via APScheduler.
"""

import logging
import math
import time
from collections import Counter
from datetime import datetime, timezone

import numpy as np

from config import TABLE_AGENTS, TABLE_SCORES, TABLE_TRADES, TABLE_FORUM_POSTS
from db import get_client

logger = logging.getLogger(__name__)

MIN_TRADES = 10


# ---------------------------------------------------------------------------
# D1: Timing regularity (weight 0.30)
# ---------------------------------------------------------------------------

def score_d1_timing(trades: list[dict]) -> tuple[float, list[str]]:
    flags = []
    timestamps = sorted(t["timestamp_ms"] for t in trades)
    intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]

    if not intervals:
        return 0.5, ["insufficient_intervals"]

    mean_iv = np.mean(intervals)
    std_iv = np.std(intervals)
    cv = std_iv / mean_iv if mean_iv > 0 else 1.0

    # CV < 0.3 = bot (0), CV > 1.5 = human (1)
    score = min(max((cv - 0.3) / 1.2, 0.0), 1.0)
    flags.append(f"timing_cv={cv:.3f}")

    # Trade frequency: trades per day — high frequency = bot signal
    time_span_days = (timestamps[-1] - timestamps[0]) / (1000 * 86400)
    if time_span_days > 0:
        trades_per_day = len(trades) / time_span_days
        flags.append(f"trades_per_day={trades_per_day:.1f}")
        # > 20 trades/day is very likely automated
        if trades_per_day > 20:
            score = max(score - 0.4, 0.0)
            flags.append("high_frequency_bot")
        elif trades_per_day > 10:
            score = max(score - 0.2, 0.0)
            flags.append("moderate_frequency")

    # Millisecond distribution check
    ms_parts = [t["timestamp_ms"] % 1000 for t in trades]
    zero_ms_pct = sum(1 for m in ms_parts if m == 0) / len(ms_parts)
    if zero_ms_pct > 0.8:
        score = max(score - 0.2, 0.0)
        flags.append(f"ms_zero_pct={zero_ms_pct:.2f}")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D2: Sleep pattern (weight 0.25)
# ---------------------------------------------------------------------------

def score_d2_sleep(trades: list[dict]) -> tuple[float, list[str]]:
    flags = []
    hours = [datetime.fromtimestamp(t["timestamp_ms"] / 1000, tz=timezone.utc).hour for t in trades]
    hour_counts = Counter(hours)

    total = sum(hour_counts.values())
    probs = [count / total for count in hour_counts.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(24)
    normalized = entropy / max_entropy

    # entropy > 0.85 = bot (0), entropy < 0.5 = human (1)
    score = min(max((0.85 - normalized) / 0.35, 0.0), 1.0)
    flags.append(f"sleep_entropy={normalized:.3f}")

    # Max consecutive hours without trades
    all_hours_set = set(hours)
    max_gap = 0
    gap = 0
    for h in range(48):  # wrap around
        if (h % 24) not in all_hours_set:
            gap += 1
            max_gap = max(max_gap, gap)
        else:
            gap = 0

    if max_gap >= 6:
        score = min(score + 0.15, 1.0)
        flags.append(f"sleep_gap={max_gap}h")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D3: Position sizing (weight 0.15)
# ---------------------------------------------------------------------------

def score_d3_sizing(trades: list[dict]) -> tuple[float, list[str]]:
    flags = []
    sizes = []
    for t in trades:
        try:
            sizes.append(float(t["size"]))
        except (ValueError, TypeError):
            continue

    if not sizes:
        return 0.5, ["no_valid_sizes"]

    def is_round(s: float) -> bool:
        for divisor in [500, 250, 100, 50, 10]:
            if s != 0 and s % divisor == 0:
                return True
        return False

    round_pct = sum(1 for s in sizes if is_round(s)) / len(sizes)
    # round_pct > 0.7 = human (1), < 0.1 = bot (0)
    score = min(max((round_pct - 0.1) / 0.6, 0.0), 1.0)
    flags.append(f"round_pct={round_pct:.2f}")

    # Unique ratio
    unique_ratio = len(set(sizes)) / len(sizes)
    if unique_ratio < 0.3:
        score = max(score - 0.15, 0.0)
        flags.append(f"low_unique_ratio={unique_ratio:.2f}")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D4: Reaction to price events (weight 0.15)
# ---------------------------------------------------------------------------

def score_d4_reaction(trades: list[dict]) -> tuple[float, list[str]]:
    """
    Price reaction analysis. This is complex and requires candle data.
    For now: set neutral and flag for future improvement.
    """
    # No candle data yet — use trade frequency as proxy
    # Agents that trade very frequently in reaction-like patterns = bot
    if not trades:
        return 0.4, ["d4_no_data"]

    timestamps = sorted(t["timestamp_ms"] for t in trades)
    # Count rapid-fire trades (< 2 min apart)
    rapid = sum(1 for i in range(len(timestamps) - 1) if timestamps[i + 1] - timestamps[i] < 120000)
    rapid_pct = rapid / max(len(timestamps) - 1, 1)

    # High rapid-fire % = bot (automated reactions)
    if rapid_pct > 0.5:
        return round(0.2, 4), [f"rapid_fire_pct={rapid_pct:.2f}"]
    elif rapid_pct > 0.3:
        return round(0.35, 4), [f"rapid_fire_pct={rapid_pct:.2f}"]
    else:
        return round(0.6, 4), [f"rapid_fire_pct={rapid_pct:.2f}"]


# ---------------------------------------------------------------------------
# D5: Forum post analysis (weight 0.10)
# ---------------------------------------------------------------------------

def score_d5_forum(agent_id: str) -> tuple[float, list[str]]:
    flags = []
    sb = get_client()
    posts = sb.table(TABLE_FORUM_POSTS).select("*").eq("agent_id", agent_id).execute().data or []

    if len(posts) < 3:
        return 0.5, ["insufficient_forum_posts"]

    lengths = [p["content_length"] for p in posts if p.get("content_length")]
    if not lengths:
        return 0.5, ["no_content_lengths"]

    mean_len = np.mean(lengths)
    std_len = np.std(lengths)
    cv = std_len / mean_len if mean_len > 0 else 0

    # cv < 0.1 = bot template (0), > 0.5 = human variability (1)
    score = min(max((cv - 0.1) / 0.4, 0.0), 1.0)
    flags.append(f"forum_length_cv={cv:.3f}")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D6: Wallet age (weight 0.05)
# ---------------------------------------------------------------------------

def score_d6_wallet(agent: dict, trades: list[dict]) -> tuple[float, list[str]]:
    flags = []
    if not trades:
        return 0.5, ["no_trades_for_wallet_age"]

    earliest_trade = min(t["timestamp_ms"] for t in trades)
    first_seen = agent.get("first_seen")

    if first_seen:
        try:
            fs_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            fs_ms = int(fs_dt.timestamp() * 1000)
            # If first trade is much earlier than first_seen on platform — diverse activity
            diff_hours = (fs_ms - earliest_trade) / (1000 * 3600)
            if diff_hours > 168:  # > 1 week before DGClaw
                score = 0.7
                flags.append("wallet_pre_existing")
            else:
                score = 0.3
                flags.append("wallet_new_to_dgclaw")
        except Exception:
            score = 0.5
            flags.append("first_seen_parse_error")
    else:
        score = 0.5
        flags.append("no_first_seen")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

WEIGHTS = {
    "d1": 0.20,
    "d2": 0.35,
    "d3": 0.20,
    "d4": 0.10,
    "d5": 0.10,
    "d6": 0.05,
}


def classify(composite: float) -> str:
    if composite < 0.25:
        return "BOT"
    elif composite < 0.40:
        return "LIKELY_BOT"
    elif composite < 0.60:
        return "UNCERTAIN"
    elif composite < 0.75:
        return "LIKELY_HUMAN"
    else:
        return "HUMAN"


def score_agent(agent: dict) -> dict | None:
    """Score one agent. Returns score row dict or None if insufficient data."""
    sb = get_client()
    trades = (
        sb.table(TABLE_TRADES)
        .select("*")
        .eq("agent_id", agent["id"])
        .order("timestamp_ms")
        .execute()
        .data or []
    )

    if len(trades) < MIN_TRADES:
        logger.info("Agent %s (%s): only %d trades, skipping", agent["id"], agent["name"], len(trades))
        return None

    d1, f1 = score_d1_timing(trades)
    d2, f2 = score_d2_sleep(trades)
    d3, f3 = score_d3_sizing(trades)
    d4, f4 = score_d4_reaction(trades)
    d5, f5 = score_d5_forum(agent["id"])
    d6, f6 = score_d6_wallet(agent, trades)

    composite = round(
        WEIGHTS["d1"] * d1
        + WEIGHTS["d2"] * d2
        + WEIGHTS["d3"] * d3
        + WEIGHTS["d4"] * d4
        + WEIGHTS["d5"] * d5
        + WEIGHTS["d6"] * d6,
        4,
    )

    all_flags = f1 + f2 + f3 + f4 + f5 + f6

    row = {
        "agent_id": agent["id"],
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "d1_timing": d1,
        "d2_sleep": d2,
        "d3_sizing": d3,
        "d4_reaction": d4,
        "d5_forum": d5,
        "d6_wallet": d6,
        "composite": composite,
        "classification": classify(composite),
        "flags": all_flags,
        "trade_count_at_scoring": len(trades),
    }

    sb.table(TABLE_SCORES).insert(row).execute()
    logger.info(
        "Agent %s (%s): composite=%.3f class=%s",
        agent["id"], agent["name"], composite, row["classification"],
    )
    return row


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run():
    """Score all eligible agents."""
    logger.info("=== Analyzer started ===")
    start = time.time()
    sb = get_client()

    agents = sb.table(TABLE_AGENTS).select("*").execute().data or []
    scored = 0
    for agent in agents:
        try:
            result = score_agent(agent)
            if result:
                scored += 1
        except Exception:
            logger.exception("Scoring failed for agent %s", agent["id"])

    elapsed = time.time() - start
    logger.info("=== Analyzer done: %d/%d agents scored in %.1fs ===", scored, len(agents), elapsed)
