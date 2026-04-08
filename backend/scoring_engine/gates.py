"""Hard gates — deterministic class overrides applied after Bayesian posterior.

Hard gates capture situations where the signal-based score is unreliable
or where a single observable fact is decisive:

- HG1: a human-entered label in scanner_labels (BOT or HUMAN) — trust it.
- HG2: persistent 24/7 operation across many days — physically impossible
  for a human, regardless of other signal mixes.
- HG3: owner-cluster coordination — >=4 agents sharing the same owner with
  near-identical behavioral fingerprints are a bot farm.
- HG4 and HG5 land in PR3 once scanner_onchain is populated.

Gates never mutate ``p_bot`` or the evidence log — they only override the
final classification label and append a tag to ``hard_gates_hit`` so the
frontend and calibration auditor can see why the class was forced.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional


def _per_day_active_hours(trades: Iterable[dict]) -> dict[str, set[int]]:
    """Return a {YYYY-MM-DD: set_of_UTC_hours} map from closed timestamps."""
    by_day: dict[str, set[int]] = defaultdict(set)
    for t in trades:
        ms = t.get("closed_at_ms")
        if ms is None:
            continue
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        by_day[dt.date().isoformat()].add(dt.hour)
    return by_day


def hg2_persistent_247(trades: list[dict]) -> bool:
    """HG2: collection period >=10 days with median active hours/day >=22
    AND at least 7 days where the agent traded in 23 or 24 distinct hours.

    The 10-day minimum prevents young agents from being force-classified.
    Values chosen so a human with an 8h+ sleep window cannot accidentally
    trigger it even on high-volume days.
    """
    by_day = _per_day_active_hours(trades)
    if len(by_day) < 10:
        return False
    counts = sorted(len(h) for h in by_day.values())
    median = counts[len(counts) // 2]
    days_23plus = sum(1 for c in counts if c >= 23)
    return median >= 22 and days_23plus >= 7


def hg3_coordinated_farm(owner_cluster: list[dict]) -> bool:
    """HG3: owner has >=4 agents AND >=3 share a near-identical fingerprint.

    Each cluster entry must carry a ``fingerprint`` tuple. Two fingerprints
    are considered "near identical" when every component matches after
    rounding to two decimals — a simple hashable proxy for the design doc's
    L1 distance < 0.2 rule that works on the scale of our fingerprint
    components (T1 median_gap in days, S1 round_pct, S6 max_freq, S2
    decimals, S7 dominant leverage).
    """
    if not owner_cluster or len(owner_cluster) < 4:
        return False
    fingerprints: list[tuple] = []
    for a in owner_cluster:
        fp = a.get("fingerprint")
        if fp is None:
            continue
        fingerprints.append(tuple(round(float(x), 2) for x in fp))
    if len(fingerprints) < 3:
        return False
    counts = Counter(fingerprints)
    return any(c >= 3 for c in counts.values())


def apply_hard_gates(
    agent: dict,
    trades: list[dict],
    owner_cluster: list[dict],
    onchain: Optional[dict],
    label: Optional[str],
    natural_class: str,
) -> tuple[str, list[str]]:
    """Return (final_class, hard_gates_hit).

    Order of precedence is tight: labels > force-BOT gates > ceiling gates >
    natural class. HG4 and HG5 are intentionally no-ops until scanner_onchain
    is populated in PR3.
    """
    hits: list[str] = []

    # HG1: explicit human label wins outright for BOT and HUMAN. SUSPICIOUS /
    # UNSURE labels are memo-only and do not force classification.
    if label in ("BOT", "HUMAN"):
        return label, ["gate:labeled"]

    # HG2: persistent 24/7 operation — always forces BOT.
    if hg2_persistent_247(trades):
        hits.append("gate:persistent_24_7")
        return "BOT", hits

    # HG3: coordinated farm inside the owner cluster.
    if owner_cluster and hg3_coordinated_farm(owner_cluster):
        hits.append(f"gate:farm_{len(owner_cluster)}")
        return "BOT", hits

    # HG4 / HG5 placeholders (Phase 4).
    # if onchain and hg4_onchain_human_ceiling(onchain) and natural_class in ("BOT", "LIKELY_BOT"):
    #     hits.append("gate:onchain_human_ceiling")
    #     return "UNCERTAIN", hits
    # if onchain and hg5_throwaway_farm(onchain, agent, trades):
    #     hits.append("gate:throwaway_farm")
    #     return "BOT", hits

    return natural_class, hits
