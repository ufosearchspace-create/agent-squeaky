"""
Reporter: sends Telegram notifications after analysis and daily summaries.
"""

import html as html_mod
import logging
import time
from datetime import datetime, timezone, timedelta

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED, TABLE_AGENTS, TABLE_SCORES
from db import get_client

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _send_telegram(text: str):
    """Send a message via Telegram Bot API. Splits if > 4096 chars."""
    if not TELEGRAM_ENABLED:
        logger.info("Telegram disabled, skipping message (%d chars)", len(text))
        return
    chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        try:
            resp = httpx.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception:
            logger.exception("Failed to send Telegram message")


def _get_latest_scores() -> list[dict]:
    """Get latest score per agent via the view."""
    sb = get_client()
    data = sb.table("scanner_agent_latest_scores").select("*").execute().data or []
    return data


def _format_agent_line(a: dict) -> str:
    cls = a.get("classification", "?")
    emoji = {"BOT": "🤖", "LIKELY_BOT": "🟠", "UNCERTAIN": "⚪", "LIKELY_HUMAN": "🟢", "HUMAN": "✅"}.get(cls, "❓")
    name = html_mod.escape(str(a.get("name", "?"))[:20])
    pnl = a.get("total_pnl", 0)
    composite = a.get("composite")
    score_str = f"{composite:.2f}" if composite is not None else "n/a"
    return f"{emoji} <b>{name}</b>  PnL: {pnl:+.1f}  Score: {score_str}  [{cls}]"


def post_analysis_report():
    """Send report after analyzer run."""
    scores = _get_latest_scores()
    if not scores:
        _send_telegram("📊 <b>Scanner Report</b>\nNo scored agents yet.")
        return

    scored = [s for s in scores if s.get("composite") is not None]
    bots = [s for s in scored if s.get("classification") in ("BOT", "LIKELY_BOT")]
    humans = [s for s in scored if s.get("classification") in ("HUMAN", "LIKELY_HUMAN")]
    uncertain = [s for s in scored if s.get("classification") == "UNCERTAIN"]

    lines = [
        f"📊 <b>Scanner Report</b> — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Scanned: {len(scores)} | Scored: {len(scored)} | 🤖 Bots: {len(bots)} | ✅ Humans: {len(humans)} | ⚪ Uncertain: {len(uncertain)}",
        "",
    ]

    # Top bots
    if bots:
        lines.append("<b>🤖 Top Bots:</b>")
        for a in sorted(bots, key=lambda x: x.get("composite", 1))[:10]:
            lines.append(_format_agent_line(a))
        lines.append("")

    # Top humans
    if humans:
        lines.append("<b>✅ Top Humans:</b>")
        for a in sorted(humans, key=lambda x: x.get("composite", 0), reverse=True)[:10]:
            lines.append(_format_agent_line(a))
        lines.append("")

    # Top 10 by PnL
    by_pnl = sorted(scored, key=lambda x: x.get("total_pnl", 0), reverse=True)[:10]
    lines.append("<b>💰 Top 10 by PnL:</b>")
    for a in by_pnl:
        lines.append(_format_agent_line(a))

    _send_telegram("\n".join(lines))
    logger.info("Post-analysis report sent")


def daily_summary():
    """Daily summary at 08:00 UTC."""
    scores = _get_latest_scores()
    scored = [s for s in scores if s.get("composite") is not None]

    # Classification distribution
    dist = {}
    for s in scored:
        cls = s.get("classification", "UNKNOWN")
        dist[cls] = dist.get(cls, 0) + 1

    lines = [
        f"📋 <b>Daily Summary</b> — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"Total agents: {len(scores)}",
        f"Scored agents: {len(scored)}",
        "",
        "<b>Classification Distribution:</b>",
    ]
    for cls in ["BOT", "LIKELY_BOT", "UNCERTAIN", "LIKELY_HUMAN", "HUMAN"]:
        count = dist.get(cls, 0)
        pct = (count / len(scored) * 100) if scored else 0
        lines.append(f"  {cls}: {count} ({pct:.0f}%)")

    # New agents in last 24h
    sb = get_client()
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    new_agents = sb.table(TABLE_AGENTS).select("id,name").gte("first_seen", yesterday).execute().data or []
    if new_agents:
        lines.append(f"\n<b>🆕 New agents (24h): {len(new_agents)}</b>")
        for a in new_agents[:20]:
            lines.append(f"  • {a.get('name', '?')}")

    _send_telegram("\n".join(lines))
    logger.info("Daily summary sent")
