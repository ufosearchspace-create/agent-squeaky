"""
Analyzer: scores each agent on 6 dimensions (D1-D6) to classify as bot or human.
Runs every 30 minutes via APScheduler.
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

MIN_TRADES = 3


# ---------------------------------------------------------------------------
# D1: Timing regularity
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

    # Trades per day for reference
    time_span_days = (timestamps[-1] - timestamps[0]) / (1000 * 86400)
    if time_span_days > 0:
        flags.append(f"trades_per_day={len(trades) / time_span_days:.1f}")

    # Millisecond distribution — bots often trade on exact seconds
    ms_parts = [t["timestamp_ms"] % 1000 for t in trades]
    zero_ms_pct = sum(1 for m in ms_parts if m == 0) / len(ms_parts)
    if zero_ms_pct > 0.8:
        score = max(score - 0.2, 0.0)
        flags.append(f"ms_zero_pct={zero_ms_pct:.2f}")

    # Interval clustering: if most intervals fall in a narrow band, that's bot-like
    if len(intervals) >= 5:
        median_iv = np.median(intervals)
        if median_iv > 0:
            within_band = sum(1 for iv in intervals if 0.5 * median_iv <= iv <= 2.0 * median_iv)
            band_pct = within_band / len(intervals)
            if band_pct > 0.8:
                score = max(score - 0.15, 0.0)
                flags.append(f"interval_cluster={band_pct:.2f}")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D2: Sleep pattern
# ---------------------------------------------------------------------------

def score_d2_sleep(trades: list[dict]) -> tuple[float, list[str]]:
    flags = []
    hours = [datetime.fromtimestamp(t["timestamp_ms"] / 1000, tz=timezone.utc).hour for t in trades]
    hour_counts = Counter(hours)
    active_hours = len(hour_counts)

    total = sum(hour_counts.values())
    probs = [count / total for count in hour_counts.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(24)
    normalized = entropy / max_entropy

    flags.append(f"sleep_entropy={normalized:.3f}")
    flags.append(f"active_hours={active_hours}")

    # Max consecutive hours WITHOUT trades (wrap-around)
    all_hours_set = set(hours)
    max_gap = 0
    gap = 0
    for h in range(48):
        if (h % 24) not in all_hours_set:
            gap += 1
            max_gap = max(max_gap, gap)
        else:
            gap = 0

    flags.append(f"sleep_gap={max_gap}h")

    # Scoring: multi-factor
    # Primary: sleep gap is the strongest signal
    if max_gap >= 8:
        score = 1.0  # Clear human sleep pattern
    elif max_gap >= 6:
        score = 0.85
    elif max_gap >= 4:
        score = 0.6
    elif max_gap >= 2:
        # Some gap but could be bot with downtime
        score = 0.35
    else:
        # Trades in nearly every hour — bot
        score = 0.0

    # Adjust by active hours coverage
    if active_hours <= 8:
        score = min(score + 0.1, 1.0)  # Only active in few hours = human
    elif active_hours >= 20:
        score = max(score - 0.1, 0.0)  # Active in almost all hours = bot

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D3: Position sizing
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
        # Also check if it's a simple number (1, 2, 5, etc.)
        if s != 0 and s == int(s) and s <= 1000:
            return True
        return False

    round_pct = sum(1 for s in sizes if is_round(s)) / len(sizes)
    flags.append(f"round_pct={round_pct:.2f}")

    # Unique ratio — how many distinct sizes
    unique_ratio = len(set(sizes)) / len(sizes) if len(sizes) > 1 else 1.0
    flags.append(f"unique_ratio={unique_ratio:.2f}")

    # Decimal precision — bots use many decimal places
    avg_decimals = np.mean([len(str(s).split('.')[-1]) if '.' in str(s) else 0 for s in sizes])
    flags.append(f"avg_decimals={avg_decimals:.1f}")

    # Multi-factor scoring
    if round_pct > 0.5:
        score = 0.9  # Majority round = human
    elif round_pct > 0.2:
        score = 0.65  # Some round = likely human
    elif round_pct > 0.05:
        score = 0.4  # Very few round = uncertain
    else:
        # Zero round sizes
        if avg_decimals > 4:
            score = 0.0  # High precision + no rounding = definite bot
            flags.append("high_precision_bot")
        else:
            score = 0.15  # No rounding but low precision

    # Penalize very low unique ratio (same size repeated = bot formula)
    if unique_ratio < 0.2 and len(sizes) >= 5:
        score = max(score - 0.2, 0.0)
        flags.append("repetitive_sizes")

    return round(score, 4), flags


# ---------------------------------------------------------------------------
# D4: Trade pattern analysis
# ---------------------------------------------------------------------------

def score_d4_pattern(trades: list[dict]) -> tuple[float, list[str]]:
    """Analyze trade patterns: coin diversity, direction patterns, timing."""
    flags = []
    if len(trades) < 3:
        return 0.5, ["d4_insufficient"]

    timestamps = sorted(t["timestamp_ms"] for t in trades)

    # Rapid-fire trades (< 2 min apart)
    rapid = sum(1 for i in range(len(timestamps) - 1) if timestamps[i + 1] - timestamps[i] < 120000)
    rapid_pct = rapid / max(len(timestamps) - 1, 1)
    flags.append(f"rapid_fire={rapid_pct:.2f}")

    # Coin diversity — humans tend to trade fewer coins
    coins = [t.get("coin", "") for t in trades]
    unique_coins = len(set(coins))
    flags.append(f"unique_coins={unique_coins}")

    # Direction pattern — bots often alternate mechanically
    directions = [t.get("side", "") for t in trades]
    if len(directions) >= 4:
        alternations = sum(1 for i in range(len(directions) - 1) if directions[i] != directions[i + 1])
        alt_pct = alternations / (len(directions) - 1)
        flags.append(f"alt_pct={alt_pct:.2f}")
    else:
        alt_pct = 0.5

    # Scoring
    score = 0.5  # start neutral

    if rapid_pct > 0.5:
        score -= 0.25  # rapid fire = bot
    elif rapid_pct > 0.3:
        score -= 0.1

    # High coin diversity with many trades = sophisticated bot
    if unique_coins >= 5 and len(trades) >= 10:
        score -= 0.1
        flags.append("multi_coin_bot")
    elif unique_coins <= 2 and len(trades) >= 10:
        score += 0.1  # Focused trading = could be human

    # Perfect alternation = mechanical
    if alt_pct > 0.85:
        score -= 0.1
        flags.append("mechanical_alternation")

    return round(min(max(score, 0.0), 1.0), 4), flags


# ---------------------------------------------------------------------------
# D5: PnL consistency
# ---------------------------------------------------------------------------

def score_d5_pnl(trades: list[dict]) -> tuple[float, list[str]]:
    """
    Analyze PnL patterns. Bots have consistent, formulaic PnL distributions.
    Humans have emotional patterns — hold losers too long, cut winners too early.
    """
    flags = []
    pnls = [t.get("closed_pnl", 0) for t in trades if t.get("closed_pnl", 0) != 0]

    if len(pnls) < 5:
        return None, ["insufficient_pnl_data"]

    abs_pnls = [abs(p) for p in pnls]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    # 1. PnL magnitude CV — bots have consistent PnL sizes, humans vary wildly
    mean_abs = np.mean(abs_pnls)
    if mean_abs > 0:
        pnl_cv = np.std(abs_pnls) / mean_abs
    else:
        pnl_cv = 0
    flags.append(f"pnl_cv={pnl_cv:.2f}")

    # 2. Win/Loss asymmetry — humans cut winners short and hold losers
    # (avg loss > avg win = human tendency, avg loss ≈ avg win = bot)
    if wins and losses:
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))
        if avg_win > 0:
            wl_ratio = avg_loss / avg_win
            flags.append(f"wl_ratio={wl_ratio:.2f}")
        else:
            wl_ratio = 1.0
    else:
        wl_ratio = 1.0

    # 3. PnL clustering — bots produce PnL in tight bands
    if len(abs_pnls) >= 5:
        median_pnl = np.median(abs_pnls)
        if median_pnl > 0:
            in_band = sum(1 for p in abs_pnls if 0.3 * median_pnl <= p <= 3.0 * median_pnl)
            band_pct = in_band / len(abs_pnls)
            flags.append(f"pnl_band={band_pct:.2f}")
        else:
            band_pct = 0.5
    else:
        band_pct = 0.5

    # Scoring
    score = 0.5

    # Low CV = consistent = bot-like
    if pnl_cv < 0.5:
        score -= 0.2
        flags.append("consistent_pnl")
    elif pnl_cv > 2.0:
        score += 0.2
        flags.append("variable_pnl")

    # Win/Loss asymmetry — ratio far from 1.0 = human emotional bias
    if wl_ratio > 1.5 or wl_ratio < 0.5:
        score += 0.15
        flags.append("asymmetric_wl")
    elif 0.8 <= wl_ratio <= 1.2:
        score -= 0.1
        flags.append("symmetric_wl")

    # Tight PnL band = bot
    if band_pct > 0.85:
        score -= 0.15
        flags.append("tight_pnl_band")

    return round(min(max(score, 0.0), 1.0), 4), flags


# ---------------------------------------------------------------------------
# D6: Wallet age
# ---------------------------------------------------------------------------

def score_d6_wallet(agent: dict, trades: list[dict]) -> tuple[float, list[str]]:
    if not trades:
        return None, ["no_wallet_data"]  # None = exclude from weighting

    earliest_trade = min(t["timestamp_ms"] for t in trades)
    first_seen = agent.get("first_seen")

    if not first_seen:
        return None, ["no_first_seen"]

    try:
        fs_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        fs_ms = int(fs_dt.timestamp() * 1000)
        diff_hours = (fs_ms - earliest_trade) / (1000 * 3600)
        if diff_hours > 168:
            return 0.7, ["wallet_pre_existing"]
        else:
            return 0.3, ["wallet_new_to_dgclaw"]
    except Exception:
        return None, ["first_seen_parse_error"]


# ---------------------------------------------------------------------------
# Composite — dynamic weighting
# ---------------------------------------------------------------------------

BASE_WEIGHTS = {
    "d1": 0.15,
    "d2": 0.30,
    "d3": 0.25,
    "d4": 0.10,
    "d5": 0.15,
    "d6": 0.05,
}


def classify(composite: float) -> str:
    if composite < 0.25:
        return "BOT"
    elif composite < 0.45:
        return "LIKELY_BOT"
    elif composite < 0.55:
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
    d4, f4 = score_d4_pattern(trades)
    d5, f5 = score_d5_pnl(trades)
    d6, f6 = score_d6_wallet(agent, trades)

    # Dynamic weighting: exclude D5/D6 when they have no data (return None)
    # and redistribute their weight to D1-D4
    scores = {"d1": d1, "d2": d2, "d3": d3, "d4": d4, "d5": d5, "d6": d6}
    active_weights = {}
    excluded_weight = 0.0

    for dim, weight in BASE_WEIGHTS.items():
        if scores[dim] is None:
            excluded_weight += weight
        else:
            active_weights[dim] = weight

    # Redistribute excluded weight proportionally to active dimensions
    if excluded_weight > 0 and active_weights:
        total_active = sum(active_weights.values())
        for dim in active_weights:
            active_weights[dim] = active_weights[dim] / total_active

    # Calculate composite
    composite = 0.0
    for dim, weight in active_weights.items():
        composite += weight * scores[dim]
    composite = round(composite, 4)

    # Store None as 0.5 for DB (display purposes)
    d5_display = d5 if d5 is not None else 0.5
    d6_display = d6 if d6 is not None else 0.5

    all_flags = f1 + f2 + f3 + f4 + f5 + f6

    # === OVERRIDE RULES ===

    classification = classify(composite)

    # Override rules — change classification but keep natural composite spread

    # Rule 1: D2 (sleep) AND D3 (sizing) both scream bot → BOT
    if d2 <= 0.1 and d3 <= 0.1:
        classification = "BOT"
        all_flags.append("override_d2d3_bot")

    # Rule 2: D3 = 0 with high precision → strong bot signal
    if d3 <= 0.05 and "high_precision_bot" in all_flags and classification == "UNCERTAIN":
        classification = "LIKELY_BOT"
        all_flags.append("override_precision_bot")

    # Rule 3: Clear sleep gap (D2 >= 0.85) → not a bot
    if d2 >= 0.85 and classification in ("UNCERTAIN", "LIKELY_BOT"):
        classification = "LIKELY_HUMAN"
        all_flags.append("override_clear_sleep")

    # Rule 4: Active in 20+ hours AND no round sizes → bot
    active_h = int(next((f.split("=")[1] for f in all_flags if f.startswith("active_hours=")), "0"))
    if active_h >= 20 and d3 <= 0.15:
        classification = "BOT"
        all_flags.append("override_allday_noround")

    row = {
        "agent_id": agent["id"],
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "d1_timing": d1,
        "d2_sleep": d2,
        "d3_sizing": d3,
        "d4_reaction": d4,
        "d5_forum": d5_display,
        "d6_wallet": d6_display,
        "composite": composite,
        "classification": classification,
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
