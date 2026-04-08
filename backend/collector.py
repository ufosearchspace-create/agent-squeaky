"""
Collector: fetches DegenClaw leaderboard and trades via DegenClaw API.
Runs every 30 minutes via APScheduler.
"""

import logging
import time
from datetime import datetime, timezone

import httpx

from config import (
    DGCLAW_API_KEY,
    TABLE_AGENTS,
    TABLE_FORUM_POSTS,
    TABLE_TRADES,
)
from db import get_client

logger = logging.getLogger(__name__)

DGCLAW_BASE = "https://degen.virtuals.io/api"
REQUEST_DELAY = 0.5  # polite delay between API calls


def _dgclaw_headers() -> dict:
    return {"Authorization": f"Bearer {DGCLAW_API_KEY}"}


# ---------------------------------------------------------------------------
# Pure parsers (testable without DB or HTTP)
# ---------------------------------------------------------------------------

def _iso_to_ms(s: str | None) -> int | None:
    """Convert an ISO-8601 timestamp (possibly with trailing Z) to epoch ms.

    Returns None for None, empty string, or unparseable input. Never raises.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return int(dt.timestamp() * 1000)


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _trade_to_row(agent_id: str, api_trade: dict) -> dict:
    """Pure parser: DegenClaw /agents/{id}/trades row -> scanner_trades row.

    DegenClaw API returns each CLOSE event with the full open+close pair
    inline (openedAt, executedAt, entryPrice, exitPrice, etc.), so one API
    row maps directly to one scanner_trades row. All fields are optional
    except the dgc_trade_id + closed_at_ms that the caller should validate.

    closed_pnl is stored as None (not 0.0) when the API omits realizedPnl
    so that B3 win/loss asymmetry does not silently count missing-PnL
    trades as "neither win nor loss" biased data.
    """
    trade_id = api_trade.get("id")
    return {
        "agent_id": agent_id,
        "dgc_trade_id": str(trade_id) if trade_id is not None else None,
        "opened_at_ms": _iso_to_ms(api_trade.get("openedAt")),
        "closed_at_ms": _iso_to_ms(api_trade.get("executedAt")),
        "coin": api_trade.get("token") or "",
        "direction": api_trade.get("direction") or "",
        "entry_price": _to_float(api_trade.get("entryPrice")),
        "exit_price": _to_float(api_trade.get("exitPrice")),
        "position_size": _to_float(api_trade.get("positionSize")),
        "leverage": _to_int(api_trade.get("leverage")),
        "closed_pnl": _to_float(api_trade.get("realizedPnl")),
    }


# ---------------------------------------------------------------------------
# Step 1: Leaderboard
# ---------------------------------------------------------------------------

def fetch_leaderboard() -> list[dict]:
    """Fetch up to 1000 agents from the DegenClaw leaderboard."""
    url = f"{DGCLAW_BASE}/leaderboard"
    try:
        resp = httpx.get(url, headers=_dgclaw_headers(), params={"limit": 1000}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, list):
            return data
        logger.warning("Unexpected leaderboard format: %s", type(data))
        return []
    except Exception:
        logger.exception("Failed to fetch leaderboard")
        return []


def upsert_agents(agents: list[dict]) -> int:
    """Upsert agents into scanner_agents. Returns count of processed agents."""
    sb = get_client()
    count = 0
    for a in agents:
        agent_id = str(a.get("id", ""))
        if not agent_id:
            continue

        perf = a.get("performance") or {}
        acp = a.get("acpAgent") or {}
        owner = a.get("owner") or {}

        row = {
            "id": agent_id,
            "name": str(a.get("name", "unknown"))[:255],
            "wallet_address": acp.get("walletAddress"),
            "agent_address": a.get("agentAddress"),
            "token_address": a.get("tokenAddress"),
            "owner_wallet": owner.get("walletAddress") if isinstance(owner, dict) else None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "trade_count": perf.get("totalTradeCount", 0),
            "win_count": perf.get("winCount", 0),
            "loss_count": perf.get("lossCount", 0),
            "total_pnl": float(perf.get("totalRealizedPnl", 0) or 0),
            "win_rate": float(perf.get("winRate", 0) or 0),
        }
        row = {k: v for k, v in row.items() if v is not None}

        try:
            sb.table(TABLE_AGENTS).upsert(row, on_conflict="id").execute()
            count += 1
        except Exception:
            logger.exception("Failed to upsert agent %s", agent_id)
    return count


# ---------------------------------------------------------------------------
# Step 2: Trades via DegenClaw API
# ---------------------------------------------------------------------------

def collect_trades_for_agent(agent_id: str) -> int:
    """Fetch trades from DegenClaw API for one agent. Returns new trade count."""
    sb = get_client()
    headers = _dgclaw_headers()

    all_trades = []
    offset = 0
    limit = 100

    # Paginate through all trades
    while True:
        try:
            resp = httpx.get(
                f"{DGCLAW_BASE}/agents/{agent_id}/trades",
                headers=headers,
                params={"limit": limit, "offset": offset},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("Failed to fetch trades for agent %s (offset %d)", agent_id, offset)
            break

        trades = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        if not trades:
            break
        all_trades.extend(trades)

        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        if not pagination.get("hasMore", False):
            break
        offset += limit
        time.sleep(REQUEST_DELAY)

    if not all_trades:
        return 0

    inserted = 0
    for t in all_trades:
        row = _trade_to_row(agent_id, t)
        if not row["dgc_trade_id"] or row["closed_at_ms"] is None:
            # Missing natural key or close timestamp — skip silently,
            # this is a malformed API row we cannot dedup against.
            continue
        try:
            sb.table(TABLE_TRADES).upsert(
                row, on_conflict="agent_id,dgc_trade_id"
            ).execute()
            inserted += 1
        except Exception:
            logger.exception("Failed to insert trade for agent %s", agent_id)
    return inserted


# ---------------------------------------------------------------------------
# Step 3: Forum posts
# ---------------------------------------------------------------------------

def collect_forum_posts_for_agent(agent_id: str) -> int:
    """Fetch forum threads and posts for one agent. Returns new post count."""
    sb = get_client()
    headers = _dgclaw_headers()

    try:
        resp = httpx.get(f"{DGCLAW_BASE}/forums/{agent_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        forums_data = resp.json()
    except Exception:
        logger.exception("Failed to fetch forums for agent %s", agent_id)
        return 0

    threads = []
    if isinstance(forums_data, list):
        threads = forums_data
    elif isinstance(forums_data, dict):
        for key in ("threads", "data", "results"):
            if key in forums_data and isinstance(forums_data[key], list):
                threads = forums_data[key]
                break

    inserted = 0
    for thread in threads:
        thread_id = str(thread.get("id", thread.get("threadId", "")))
        thread_type = thread.get("type", thread.get("threadType", "DISCUSSION"))
        if not thread_id:
            continue

        try:
            resp = httpx.get(
                f"{DGCLAW_BASE}/forums/{agent_id}/threads/{thread_id}/posts",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            posts_data = resp.json()
        except Exception:
            logger.exception("Failed to fetch posts for agent %s thread %s", agent_id, thread_id)
            continue

        posts = posts_data if isinstance(posts_data, list) else posts_data.get("posts", posts_data.get("data", []))

        for p in posts:
            post_id = str(p.get("id", p.get("postId", "")))
            if not post_id:
                continue
            content = p.get("content", p.get("body", ""))
            row = {
                "id": post_id,
                "agent_id": agent_id,
                "thread_type": thread_type,
                "title": p.get("title", ""),
                "content_length": len(content) if content else 0,
                "created_at": p.get("createdAt", p.get("created_at")),
            }
            row = {k: v for k, v in row.items() if v is not None}
            try:
                sb.table(TABLE_FORUM_POSTS).upsert(row, on_conflict="id").execute()
                inserted += 1
            except Exception:
                logger.exception("Failed to insert forum post %s", post_id)
    return inserted


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run():
    """Full collection cycle."""
    logger.info("=== Collector started ===")
    start = time.time()

    # Step 1: Leaderboard
    agents_raw = fetch_leaderboard()
    logger.info("Fetched %d agents from leaderboard", len(agents_raw))
    upserted = upsert_agents(agents_raw)
    logger.info("Upserted %d agents", upserted)

    # Load all agents from DB for trade/forum collection
    sb = get_client()
    db_agents = sb.table(TABLE_AGENTS).select("*").execute().data or []

    # Step 2: Trades via DegenClaw API
    total_trades = 0
    for agent in db_agents:
        try:
            n = collect_trades_for_agent(agent["id"])
            total_trades += n
            if n > 0:
                logger.info("Agent %s (%s): %d trades collected", agent["id"], agent["name"], n)
        except Exception:
            logger.exception("Trade collection failed for agent %s", agent["id"])
        time.sleep(REQUEST_DELAY)

    # Step 3: Forum posts
    total_posts = 0
    for agent in db_agents:
        try:
            n = collect_forum_posts_for_agent(agent["id"])
            total_posts += n
        except Exception:
            logger.exception("Forum collection failed for agent %s", agent["id"])

    elapsed = time.time() - start
    logger.info("=== Collector done: %d agents, %d trades, %d posts in %.1fs ===", len(db_agents), total_trades, total_posts, elapsed)
