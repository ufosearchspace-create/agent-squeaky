"""Meta / relative signals.

M4 (owner_cluster_size) is pure metadata surfaced by the analyzer; it
is not registered here because it does not produce an EvidenceScore.
M1-M3 and M6 land in PR3 together with scanner_onchain enrichment.
"""
from __future__ import annotations

from ..base import EvidenceScore, SignalContext
from ..calibration import get_lr


# ---------------------------------------------------------------------------
# M5 cross_agent_behavioral_consistency
# ---------------------------------------------------------------------------

def signal_m5_cross_agent_consistency(ctx: SignalContext) -> EvidenceScore | None:
    """Compare this agent's fingerprint against its owner-cluster centroid.

    The expected behavior is that siblings of the same owner run the same
    strategy (bot farm). When the target agent diverges from the cluster
    centroid it raises the probability that its owner is also trading
    manually on this specific agent — a weak human signal.

    Cluster entries are dicts with an "id" and optional "fingerprint"
    tuple. The analyzer loads siblings + fingerprints before calling the
    signal; fingerprints are a tuple of five numbers:
    (T1 median_gap_h, S1 round_pct, S6 max_single_freq, S2 avg_decimals, S7 dominant_leverage).
    """
    cluster = ctx.owner_cluster or []
    if len(cluster) < 2:
        return None

    target_id = ctx.agent.get("id")
    target_fp = None
    sibling_fps: list[tuple[float, ...]] = []
    for entry in cluster:
        fp = entry.get("fingerprint")
        if fp is None:
            continue
        fp_tuple = tuple(float(x) for x in fp)
        if entry.get("id") == target_id:
            target_fp = fp_tuple
        else:
            sibling_fps.append(fp_tuple)

    if target_fp is None or not sibling_fps:
        return None

    # Centroid = per-dimension mean across siblings.
    dim = len(target_fp)
    centroid = tuple(
        sum(fp[i] for fp in sibling_fps) / len(sibling_fps) for i in range(dim)
    )
    # Normalize each dimension by (max - min) across the whole cluster so
    # the L1 distance is scale-free. Avoids a single large-scale dimension
    # (decimal count, leverage) dominating the sum.
    all_fps = sibling_fps + [target_fp]
    ranges = [
        max(fp[i] for fp in all_fps) - min(fp[i] for fp in all_fps) or 1.0
        for i in range(dim)
    ]
    distance = sum(abs(target_fp[i] - centroid[i]) / ranges[i] for i in range(dim)) / dim

    if distance > 0.4:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="M5_cross_agent_consistency",
        log_lr_bits=get_lr("M5_cross_agent_consistency", state),
        value={
            "distance": round(distance, 3),
            "cluster_size": len(sibling_fps) + 1,
        },
        state=state,
        detail=f"fingerprint distance {distance:.2f} from cluster centroid ({len(sibling_fps) + 1} agents)",
    )


ALL_META_SIGNALS = [
    signal_m5_cross_agent_consistency,
]
