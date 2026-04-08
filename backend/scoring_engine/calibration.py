"""In-memory cache of active signal likelihood ratios.

The analyzer calls reload_cache() once at the start of a scoring run and
each signal module resolves its log_lr_bits via get_lr(signal_name, state).
Storing LRs in a DB table (scanner_signal_lrs) makes calibration a pure
data update — no code deploy needed to retune.
"""
from __future__ import annotations

from config import TABLE_SIGNAL_LRS
from db import get_client

# Module-level cache. Safe under the current BlockingScheduler (single
# job thread at a time). If we ever switch to BackgroundScheduler or run
# multiple analyzer workers, wrap _CACHE/_VERSION writes in a lock.
_CACHE: dict[str, dict[str, float]] = {}
_VERSION: int = 0


def reload_cache() -> int:
    """Load all active rows from scanner_signal_lrs into the in-memory cache.

    Returns:
        The maximum version number observed among active rows. This is the
        ``lr_version`` value the analyzer stamps onto each scanner_scores
        row for later reproducibility.
    """
    global _CACHE, _VERSION
    sb = get_client()
    rows = (
        sb.table(TABLE_SIGNAL_LRS)
        .select("*")
        .eq("active", True)
        .execute()
        .data
        or []
    )
    new_cache: dict[str, dict[str, float]] = {}
    max_v = 0
    for r in rows:
        states = (r.get("thresholds") or {}).get("states") or {}
        new_cache[r["signal_name"]] = {k: float(v) for k, v in states.items()}
        v = r.get("version") or 0
        if v > max_v:
            max_v = v
    _CACHE = new_cache
    _VERSION = max_v
    return max_v


def get_lr(signal_name: str, state: str) -> float:
    """Return the log2-LR bits for a signal/state pair.

    Missing signals or missing states return 0.0 (no evidence contribution)
    so a partially-seeded cache never crashes the analyzer — it just silently
    ignores the unknown entry. The analyzer logs the state regardless, so
    missing entries become visible during calibration review.
    """
    return _CACHE.get(signal_name, {}).get(state, 0.0)


def current_version() -> int:
    """Return the max version observed during the last reload_cache() call."""
    return _VERSION


def _reset_for_tests() -> None:
    """Test-only helper: wipe the module-level cache between test runs."""
    global _CACHE, _VERSION
    _CACHE = {}
    _VERSION = 0
