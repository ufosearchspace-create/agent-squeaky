"""
Collector: fetches DegenClaw leaderboard, Hyperliquid trades, and forum posts.
Runs every 1 hour via APScheduler.
"""

import logging
import time
from datetime import datetime, timezone

import httpx

from config import (
    DGCLAW_API_KEY,
    HYPERLIQUID_API_URL,
    TABLE_AGENTS,
    TABLE_FORUM_POSTS,
    TABLE_TRADES,
)
from db import get_client

logger = logging.getLogger(__name__)

DGCLAW_BASE = "https://degen.virtuals.io/api"
HL_THROTTLE_SEC = 1.0


def _dgclaw_headers() -> dict:
    return {"Authorization": f"Bearer {DGCLAW_API_KEY}"}


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
        logger.info("Leaderboard raw response keys: %s", list(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]")
        # Response could be a list or a dict with a data key — handle both
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Try common wrapper keys
            for key in ("data", "agents", "results", "leaderboard"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            logger.warning("Unexpected leaderboard dict keys: %s — returning empty", list(data.keys()))
        return []
    except Exception:
        logger.exception("Failed to fetch leaderboard")
        return []


def upsert_agents(agents: list[dict]) -> int:
    """Upsert agents into scanner_agents. Returns count of processed agents."""
    sb = get_client()
    count = 0
    for a in agents:
        agent_id = str(a.get("id", a.get("agentId", a.get("_id", ""))))
        if not agent_id:
            logger.warning("Agent missing id: %s", a)
            continue

        row = {
            "id": agent_id,
            "name": a.get("name", a.get("agentName", "unknown")),
            "wallet_address": a.get("walletAddress", a.get("wallet_address")),
            "agent_address": a.get("agentAddress", a.get("agent_address")),
            "token_address": a.get("tokenAddress", a.get("token_address")),
            "owner_wallet": a.get("ownerWalletAddress", a.get("owner", {}).get("walletAddress") if isinstance(a.get("owner"), dict) else None),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "trade_count": a.get("tradeCount", a.get("trade_count", 0)),
            "win_count": a.get("winCount", a.get("win_count", 0)),
            "loss_count": a.get("lossCount", a.get("loss_count", 0)),
            "total_pnl": float(a.get("pnl", a.get("totalPnl", a.get("total_pnl", 0))) or 0),
            "win_rate": float(a.get("winRate", a.get("win_rate", 0)) or 0),
        }
        # Remove None values to let DB defaults work
        row = {k: v for k, v in row.items() if v is not None}

        try:
            sb.table(TABLE_AGENTS).upsert(row, on_conflict="id").execute()
            count += 1
        except Exception:
            logger.exception("Failed to upsert agent %s", agent_id)
    return count


# ---------------------------------------------------------------------------
# Step 2: Hyperliquid trades
# ---------------------------------------------------------------------------

def _resolve_hl_wallet(agent: dict) -> str | None:
    """Try multiple wallet fields to find one with Hyperliquid fills."""
    candidates = [
        agent.get("hl_wallet"),
        agent.get("wallet_address"),
        agent.get("agent_address"),
        agent.get("owner_wallet"),
    ]
    for wallet in candidates:
        if not wallet:
            continue
        fills = _fetch_hl_fills(wallet, limit=1)
        if fills:
            logger.info("Agent %s: HL wallet resolved to %s (from candidate)", agent["id"], wallet)
            return wallet
        time.sleep(HL_THROTTLE_SEC)
    logger.warning("Agent %s: no HL wallet found among candidates %s", agent["id"], [c for c in candidates if c])
    return None


def _fetch_hl_fills(wallet: str, limit: int | None = None) -> list[dict]:
    """Fetch user fills from Hyperliquid."""
    payload = {"type": "userFills", "user": wallet}
    try:
        resp = httpx.post(HYPERLIQUID_API_URL, json=payload, timeout=30)
        resp.raise_for_status()
        fills = resp.json()
        if not isinstance(fills, list):
            logger.warning("HL fills unexpected format for %s: %s", wallet, type(fills))
            return []
        if limit:
            return fills[:limit]
        return fills
    except Exception:
        logger.exception("Failed to fetch HL fills for %s", wallet)
        return []


def collect_trades_for_agent(agent: dict) -> int:
    """Fetch and store Hyperliquid trades for one agent. Returns new trade count."""
    sb = get_client()

    # Resolve HL wallet if not yet known
    wallet = agent.get("hl_wallet")
    if not wallet:
        wallet = _resolve_hl_wallet(agent)
        if wallet:
            sb.table(TABLE_AGENTS).update({"hl_wallet": wallet}).eq("id", agent["id"]).execute()
        else:
            return 0

    fills = _fetch_hl_fills(wallet)
    if not fills:
        return 0

    inserted = 0
    for f in fills:
        row = {
            "agent_id": agent["id"],
            "timestamp_ms": int(f.get("time", 0)),
            "coin": f.get("coin", ""),
            "side": f.get("side", ""),
            "direction": f.get("dir"),
            "price": float(f.get("px", 0)),
            "size": str(f.get("sz", "0")),
            "closed_pnl": float(f.get("closedPnl", 0)),
        }
        try:
            sb.table(TABLE_TRADES).upsert(
                row, on_conflict="agent_id,timestamp_ms,coin,side,size"
            ).execute()
            inserted += 1
        except Exception:
            logger.exception("Failed to insert trade for agent %s", agent["id"])
    return inserted


# ---------------------------------------------------------------------------
# Step 3: Forum posts
# ---------------------------------------------------------------------------

def collect_forum_posts_for_agent(agent_id: str) -> int:
    """Fetch forum threads and posts for one agent. Returns new post count."""
    sb = get_client()
    headers = _dgclaw_headers()

    # Get forums/threads for agent
    try:
        resp = httpx.get(f"{DGCLAW_BASE}/forums/{agent_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        forums_data = resp.json()
    except Exception:
        logger.exception("Failed to fetch forums for agent %s", agent_id)
        return 0

    # Extract thread IDs — response structure unknown, log and adapt
    threads = []
    if isinstance(forums_data, list):
        threads = forums_data
    elif isinstance(forums_data, dict):
        for key in ("threads", "data", "results"):
            if key in forums_data and isinstance(forums_data[key], list):
                threads = forums_data[key]
                break
        if not threads:
            logger.info("Forum response for %s keys: %s", agent_id, list(forums_data.keys()))

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

    # Step 2: Trades
    total_trades = 0
    for agent in db_agents:
        try:
            n = collect_trades_for_agent(agent)
            total_trades += n
            logger.info("Agent %s (%s): %d trades collected", agent["id"], agent["name"], n)
        except Exception:
            logger.exception("Trade collection failed for agent %s", agent["id"])
        time.sleep(HL_THROTTLE_SEC)

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
