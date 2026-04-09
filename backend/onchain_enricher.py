"""On-chain enrichment for owner wallets via Basescan HTML scraping.

Runs once a day. For every distinct owner_wallet in scanner_agents that
has not been refreshed in 7+ days, fetches
``https://basescan.org/address/{addr}`` and parses the server-rendered
`<meta name="Description">` tag plus the `First:` block. The result goes
into scanner_onchain where the analyzer picks it up on the next cycle.

Design notes:
    * Etherscan V2 free tier does NOT cover Base, so Basescan HTML is
      the only cheap data path. Parser is anchored to stable elements
      (meta description + First: label) that have been present in
      Basescan HTML for years.
    * Polite 2 s delay between requests, rotating User-Agent across
      three realistic desktop strings so we look like the casual open
      of an address page rather than a headless scraper farm.
    * Cloudflare-aware: a 403 increments a per-run block counter, and
      if more than BLOCK_ABORT_THRESHOLD owners hit 403 in one cycle
      we abort the whole run and log ERROR. This surfaces a real outage
      fast instead of quietly writing zeros.
    * Dead EOA vs throwaway: the parser returns ``total_tx_count = 0``
      and ``age_days = None`` for wallets with no Base activity. The
      analyzer signals treat that as "insufficient data" (None evidence
      entry), not as a bot indicator.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import httpx

from config import TABLE_AGENTS, TABLE_ONCHAIN
from db import get_client

logger = logging.getLogger(__name__)

BASESCAN_URL_TEMPLATE = "https://basescan.org/address/{addr}"

#: Seconds between successful requests. Pre-pla plan number was 2 s.
REQUEST_DELAY_S = 2.0

#: Maximum retries on transport errors or 429/5xx.
MAX_RETRIES = 3

#: How many 403s in a single run before we abort the whole cycle and
#: log an error. Catches Cloudflare lockouts before they spin the wheels.
BLOCK_ABORT_THRESHOLD = 10

#: Only refresh owners that have not been enriched in the last N days.
REFRESH_INTERVAL_DAYS = 7

#: Small pool of realistic desktop User-Agents.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
]


# ---------------------------------------------------------------------------
# Regex anchors (compile once)
# ---------------------------------------------------------------------------

_META_DESC_RE = re.compile(
    r'<meta\s+name=["\']Description["\']\s+content=["\']'
    r'Address\s*'
    r'(?:\(([^)]*)\))?'
    r'[^"\']*?'
    r'\|\s*Balance:\s*\$?([\d,\.]+)'
    r'(?:\s*across\s+(\d+)\s+Chains?)?'
    r'[^"\']*?'
    r'\|\s*Transactions:\s*([\d,]+)',
    re.IGNORECASE,
)

_FIRST_TX_RE = re.compile(
    r'First:.{0,600}?<span[^>]*>([^<]+ago)</span>',
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure parsers (testable without network or DB)
# ---------------------------------------------------------------------------

def _parse_relative_age_days(s: str | None) -> int | None:
    """Convert Basescan's relative age string to whole days.

    Examples:
        "1 yr 351 days ago" -> 716
        "57 days ago"       -> 57
        "1 day ago"         -> 1
        "3 hrs ago"         -> 0   (same-day is 0 days old)
        "12 mins ago"       -> 0
        None / "" / garbage -> None
    """
    if not s:
        return None
    text = s.strip().lower()
    if "ago" not in text:
        return None
    years = 0
    days = 0
    y = re.search(r"(\d+)\s*yr", text)
    if y:
        years = int(y.group(1))
    d = re.search(r"(\d+)\s*day", text)
    if d:
        days = int(d.group(1))
    if years == 0 and days == 0:
        # Sub-day durations: hrs, mins, secs — treat as 0 days.
        if re.search(r"(hr|hour|min|sec)", text):
            return 0
        return None
    return years * 365 + days


def _parse_basescan_html(owner_wallet: str, html: str) -> dict | None:
    """Parse a Basescan address page HTML into a scanner_onchain row.

    Returns None when the page is a Cloudflare challenge, a malformed
    meta tag, or any other page where the mandatory anchors are missing.
    """
    if not html or "Attention Required" in html or "Cloudflare Ray ID" in html:
        return None

    m = _META_DESC_RE.search(html)
    if not m:
        return None

    address_kind = (m.group(1) or "").strip() or None
    balance_str = m.group(2)
    chains_str = m.group(3)
    tx_str = m.group(4)

    try:
        balance_usd = float(balance_str.replace(",", ""))
    except (TypeError, ValueError):
        balance_usd = None
    try:
        chains_active = int(chains_str) if chains_str is not None else 0
    except (TypeError, ValueError):
        chains_active = 0
    try:
        total_tx_count = int(tx_str.replace(",", ""))
    except (TypeError, ValueError):
        total_tx_count = 0

    # First-tx age only exists on wallets with at least one tx.
    age_days: int | None = None
    first_m = _FIRST_TX_RE.search(html)
    if first_m:
        age_days = _parse_relative_age_days(first_m.group(1))

    return {
        "owner_wallet": owner_wallet,
        "age_days": age_days,
        "total_tx_count": total_tx_count,
        "chains_active": chains_active,
        "balance_usd": balance_usd,
        "address_kind": address_kind,
        "source": "basescan.org",
    }


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def _fetch_basescan_html(
    client: httpx.Client,
    owner_wallet: str,
) -> tuple[str | None, int | None]:
    """Single GET with exp backoff and rotating User-Agent.

    Returns ``(html, status_code)``. On permanent failure returns
    ``(None, last_status_code)`` — the caller uses the status to decide
    whether to record a soft skip or to abort the whole cycle.
    """
    url = BASESCAN_URL_TEMPLATE.format(addr=owner_wallet)
    last_status: int | None = None
    for attempt in range(MAX_RETRIES):
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        try:
            resp = client.get(url, headers=headers, timeout=30)
            last_status = resp.status_code
            if resp.status_code == 200:
                time.sleep(REQUEST_DELAY_S)
                return resp.text, 200
            if resp.status_code == 403:
                logger.warning(
                    "Basescan 403 for %s — treating as blocked", owner_wallet,
                )
                return None, 403
            if resp.status_code in (429, 500, 502, 503, 504):
                backoff = max(10, 2 ** attempt)
                logger.warning(
                    "Basescan %d for %s, attempt %d, sleeping %ds",
                    resp.status_code, owner_wallet, attempt + 1, backoff,
                )
                time.sleep(backoff)
                continue
            # Other 4xx — non-retryable.
            logger.error(
                "Basescan non-retryable %d for %s", resp.status_code, owner_wallet,
            )
            return None, resp.status_code
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            backoff = 2 ** attempt
            logger.warning(
                "Basescan transport error for %s: %s — sleeping %ds",
                owner_wallet, exc, backoff,
            )
            time.sleep(backoff)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Basescan unexpected error for %s: %s", owner_wallet, exc,
            )
            return None, None
    logger.error(
        "Basescan exhausted %d retries for %s (last status %s)",
        MAX_RETRIES, owner_wallet, last_status,
    )
    return None, last_status


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_owners_needing_refresh(sb: Any) -> list[str]:
    """Return distinct owner_wallet values that need an on-chain refresh.

    We consider an owner "stale" when either scanner_onchain has no row
    for it yet, or the existing row is older than REFRESH_INTERVAL_DAYS.
    """
    all_owners_rows = (
        sb.table(TABLE_AGENTS)
        .select("owner_wallet")
        .execute()
        .data
        or []
    )
    owners = sorted({
        r["owner_wallet"]
        for r in all_owners_rows
        if r.get("owner_wallet")
    })
    if not owners:
        return []

    fresh_rows = (
        sb.table(TABLE_ONCHAIN)
        .select("owner_wallet,last_refreshed_at")
        .execute()
        .data
        or []
    )
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=REFRESH_INTERVAL_DAYS)
    fresh_set: set[str] = set()
    for r in fresh_rows:
        ts = r.get("last_refreshed_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt > cutoff:
            fresh_set.add(r["owner_wallet"])

    return [o for o in owners if o not in fresh_set]


def _upsert_onchain_row(sb: Any, row: dict) -> bool:
    if not row.get("owner_wallet"):
        return False
    try:
        sb.table(TABLE_ONCHAIN).upsert(
            row, on_conflict="owner_wallet"
        ).execute()
        return True
    except Exception:
        logger.exception("Failed to upsert onchain row for %s", row.get("owner_wallet"))
        return False


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run() -> None:
    """One enrichment cycle. Called by APScheduler daily."""
    logger.info("=== Onchain enricher started ===")
    start = time.time()
    sb = get_client()

    owners = _load_owners_needing_refresh(sb)
    if not owners:
        logger.info("Onchain enricher: no stale owners, done")
        return
    logger.info("Onchain enricher: %d stale owners to refresh", len(owners))

    enriched = 0
    skipped = 0
    blocked = 0

    with httpx.Client(follow_redirects=True) as client:
        for owner in owners:
            html, status = _fetch_basescan_html(client, owner)
            if status == 403:
                blocked += 1
                if blocked >= BLOCK_ABORT_THRESHOLD:
                    logger.error(
                        "Onchain enricher aborting — %d 403s in one cycle, "
                        "Cloudflare likely on",
                        blocked,
                    )
                    break
                continue
            if html is None:
                skipped += 1
                continue
            row = _parse_basescan_html(owner_wallet=owner, html=html)
            if row is None:
                skipped += 1
                continue
            if _upsert_onchain_row(sb, row):
                enriched += 1
            else:
                skipped += 1

    elapsed = time.time() - start
    logger.info(
        "=== Onchain enricher done: %d enriched, %d skipped, %d blocked in %.1fs ===",
        enriched, skipped, blocked, elapsed,
    )


__all__ = [
    "run",
    "_parse_basescan_html",
    "_parse_relative_age_days",
    "_fetch_basescan_html",
    "_load_owners_needing_refresh",
    "BASESCAN_URL_TEMPLATE",
    "REQUEST_DELAY_S",
    "REFRESH_INTERVAL_DAYS",
]
