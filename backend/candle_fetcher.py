"""
Candle fetcher: pulls Hyperliquid 5m OHLCV candles for the coins Agent
Squeaky agents actively trade, writes them to scanner_candles, and
enforces a 30-day TTL. Runs every 30 minutes, scheduled to fire slightly
before analyzer so scored agents see fresh candles.

Strategy:
    1. Query scanner_trades for the last 15 days to find which coins
       are in active use.
    2. Pick the minimum set whose cumulative trade count covers 95% of
       the total (dynamic long-tail trim).
    3. For each coin, either backfill the last 15 days (if fewer than
       1000 candles exist) or incrementally fetch from the latest
       stored ts to now().
    4. Sleep 0.3 s between every successful HTTP call. Exponential
       backoff with up to three retries on 429 / 5xx.
    5. At the end, delete candles older than 30 days (Python fallback
       for environments without pg_cron).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import TABLE_CANDLES, TABLE_TRADES
from db import get_client

logger = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

INTERVAL = "5m"
INTERVAL_MS = 5 * 60 * 1000
BACKFILL_DAYS = 15
INCREMENTAL_MIN_ROWS = 1000
COVERAGE_TARGET = 0.95
REQUEST_DELAY_S = 0.3
MAX_RETRIES = 3
RETENTION_DAYS = 30
LOOKBACK_WINDOW_DAYS = 15  # for coin selection

# Hyperliquid candleSnapshot returns up to ~5000 candles per request, but
# we chunk in 5-day windows to stay well under any undocumented limit.
CHUNK_MS = 5 * 24 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Pure parsers (testable without DB or HTTP)
# ---------------------------------------------------------------------------

def _to_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _candle_to_row(api_candle: dict) -> dict:
    """Pure parser: Hyperliquid candleSnapshot row -> scanner_candles row.

    Hyperliquid returns: {t, T, s, i, o, h, l, c, v, n} with numeric
    OHLCV stored as JSON strings. Missing fields map to None so pathological
    server responses do not crash the upsert.
    """
    return {
        "coin": api_candle.get("s") or "",
        "interval": api_candle.get("i") or INTERVAL,
        "ts_ms": int(api_candle["t"]) if api_candle.get("t") is not None else 0,
        "open": _to_float(api_candle.get("o")),
        "high": _to_float(api_candle.get("h")),
        "low": _to_float(api_candle.get("l")),
        "close": _to_float(api_candle.get("c")),
        "volume": _to_float(api_candle.get("v")),
    }


def _select_coins_by_coverage(
    coin_counts: list[dict],
    coverage: float = COVERAGE_TARGET,
) -> list[str]:
    """Pick the smallest set of coins whose cumulative trade count
    covers the given fraction of the total.

    Input is a list of ``{"coin": str, "n": int}`` already sorted by ``n``
    descending, or unsorted — the function sorts defensively. Zero or
    negative counts are ignored.
    """
    clean = [c for c in coin_counts if c.get("n") and c["n"] > 0 and c.get("coin")]
    if not clean:
        return []
    clean.sort(key=lambda c: c["n"], reverse=True)
    total = sum(c["n"] for c in clean)
    target = coverage * total
    selected: list[str] = []
    running = 0
    for c in clean:
        selected.append(c["coin"])
        running += c["n"]
        if running >= target:
            break
    return selected


def _should_backfill(existing_count: int, min_rows: int = INCREMENTAL_MIN_ROWS) -> bool:
    """Return True when the stored row count is small enough that a full
    backfill is cheaper than a series of incremental pulls.
    """
    return existing_count < min_rows


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def _fetch_candles(
    coin: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Single Hyperliquid candleSnapshot call with retry + backoff.

    Returns the raw API candle dicts (list) on success, or an empty list
    after exhausting retries. The caller is responsible for converting to
    scanner_candles rows via ``_candle_to_row``.
    """
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.post(HYPERLIQUID_INFO_URL, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                time.sleep(REQUEST_DELAY_S)
                return data
            logger.warning("Unexpected candle response for %s: %s", coin, type(data))
            return []
        except Exception as exc:
            backoff = 2 ** attempt
            logger.warning(
                "candleSnapshot %s attempt %d failed: %s — sleeping %ds",
                coin, attempt + 1, exc, backoff,
            )
            time.sleep(backoff)
    logger.error("candleSnapshot %s failed after %d retries", coin, MAX_RETRIES)
    return []


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_coin_counts(sb: Any, since_ms: int) -> list[dict]:
    """Return [{'coin': str, 'n': int}] for trades with closed_at_ms >= since_ms."""
    # supabase-py does not expose GROUP BY directly; we lean on the REST
    # rpc pattern via a select + client-side aggregation. For 11k trades
    # this is fine (single HTTP call, one pass).
    rows = (
        sb.table(TABLE_TRADES)
        .select("coin")
        .gte("closed_at_ms", since_ms)
        .execute()
        .data
        or []
    )
    counts: dict[str, int] = {}
    for r in rows:
        coin = r.get("coin")
        if not coin:
            continue
        counts[coin] = counts.get(coin, 0) + 1
    return [{"coin": k, "n": v} for k, v in counts.items()]


def _existing_candle_stats(sb: Any, coin: str) -> tuple[int, int | None]:
    """Return (row_count, max_ts_ms) for a coin/interval pair."""
    rows = (
        sb.table(TABLE_CANDLES)
        .select("ts_ms")
        .eq("coin", coin)
        .eq("interval", INTERVAL)
        .order("ts_ms", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    max_ts = rows[0]["ts_ms"] if rows else None
    # A second query for the count. Supabase-py's .count('exact') would
    # do this in one round-trip but we keep it simple and explicit.
    count_rows = (
        sb.table(TABLE_CANDLES)
        .select("ts_ms", count="exact")
        .eq("coin", coin)
        .eq("interval", INTERVAL)
        .limit(1)
        .execute()
    )
    count = getattr(count_rows, "count", None) or 0
    return count, max_ts


def _upsert_candles(sb: Any, rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    # Hyperliquid returns time-sorted ascending; the natural PK is
    # (coin, interval, ts_ms) so on-conflict is idempotent.
    for row in rows:
        if not row.get("coin") or row.get("ts_ms") is None:
            continue
        try:
            sb.table(TABLE_CANDLES).upsert(
                row, on_conflict="coin,interval,ts_ms"
            ).execute()
            inserted += 1
        except Exception:
            logger.exception("Failed to upsert candle %s @ %s", row.get("coin"), row.get("ts_ms"))
    return inserted


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _fetch_range_in_chunks(coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """Walk [start_ms, end_ms] in CHUNK_MS windows and concatenate the
    returned candles. Deduplication happens naturally via the upsert.
    """
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + CHUNK_MS, end_ms)
        out.extend(_fetch_candles(coin, INTERVAL, cursor, chunk_end))
        cursor = chunk_end
    return out


def _cleanup_old_candles(sb: Any) -> int:
    cutoff = _now_ms() - RETENTION_DAYS * 24 * 60 * 60 * 1000
    try:
        res = sb.table(TABLE_CANDLES).delete().lt("ts_ms", cutoff).execute()
        deleted = len(getattr(res, "data", []) or [])
        if deleted:
            logger.info("Cleanup deleted %d candles older than %d days", deleted, RETENTION_DAYS)
        return deleted
    except Exception:
        logger.exception("Candle TTL cleanup failed")
        return 0


def run() -> None:
    """Full candle-fetch cycle. Called by APScheduler every 30 minutes."""
    logger.info("=== Candle fetcher started ===")
    start = time.time()
    sb = get_client()

    now = _now_ms()
    since_ms = now - LOOKBACK_WINDOW_DAYS * 24 * 60 * 60 * 1000

    coin_counts = _load_coin_counts(sb, since_ms)
    selected = _select_coins_by_coverage(coin_counts, COVERAGE_TARGET)
    if not selected:
        logger.warning("No active coins found in the last %d days", LOOKBACK_WINDOW_DAYS)
        return
    logger.info("Selected %d coins (95%% coverage of %d total)", len(selected), len(coin_counts))

    total_fetched = 0
    total_upserted = 0

    for coin in selected:
        existing_count, max_ts = _existing_candle_stats(sb, coin)
        if _should_backfill(existing_count):
            backfill_start = now - BACKFILL_DAYS * 24 * 60 * 60 * 1000
            raw = _fetch_range_in_chunks(coin, backfill_start, now)
            logger.info("Backfill %s: %d candles", coin, len(raw))
        else:
            # Incremental: skip forward from the last stored candle close.
            assert max_ts is not None
            incremental_start = max_ts + INTERVAL_MS
            if incremental_start >= now:
                continue
            raw = _fetch_candles(coin, INTERVAL, incremental_start, now)
            if raw:
                logger.debug("Incremental %s: %d candles", coin, len(raw))

        total_fetched += len(raw)
        rows = [_candle_to_row(c) for c in raw]
        total_upserted += _upsert_candles(sb, rows)

    _cleanup_old_candles(sb)

    elapsed = time.time() - start
    logger.info(
        "=== Candle fetcher done: %d coins, %d fetched, %d upserted in %.1fs ===",
        len(selected), total_fetched, total_upserted, elapsed,
    )


__all__ = [
    "run",
    "_candle_to_row",
    "_select_coins_by_coverage",
    "_should_backfill",
    "_fetch_candles",
    "HYPERLIQUID_INFO_URL",
    "INTERVAL",
    "COVERAGE_TARGET",
]
