"""On-chain owner wallet signals M1 / M2 / M3 / M6.

All four read from ``ctx.onchain``, a dict projected by the analyzer
from scanner_onchain for the current agent's owner_wallet. Missing or
zero-activity onchain data produces a ``None`` evidence entry so the
Bayesian aggregator does not penalise agents whose owners we have
simply never scraped yet.

Distinction between "dead EOA" and "throwaway":
    * ``total_tx_count == 0``: dead EOA — legitimate pattern where the
      operator never touched the owner address directly. All four
      signals return None (insufficient data) except M6 which returns
      an explicit neutral so the classifier doesn't hallucinate a bot.
    * ``total_tx_count > 0 and age_days < 14``: throwaway — likely a
      burner that bridged in, created the agent, and was abandoned.
      M6 marks it medium_bot.
"""
from __future__ import annotations

import math
from typing import Optional

from ..base import EvidenceScore, SignalContext
from ..calibration import get_lr


# ---------------------------------------------------------------------------
# M1 owner_wallet_age
# ---------------------------------------------------------------------------

def signal_m1_owner_wallet_age(ctx: SignalContext) -> Optional[EvidenceScore]:
    onchain = ctx.onchain or {}
    age = onchain.get("age_days")
    txs = onchain.get("total_tx_count") or 0
    if age is None or txs == 0:
        return None  # dead EOA or not yet enriched

    if age >= 365:
        state = "medium_human"
    elif age >= 180:
        state = "weak_human"
    elif age < 14:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="M1_owner_wallet_age",
        log_lr_bits=get_lr("M1_owner_wallet_age", state),
        value={"age_days": age, "total_tx_count": txs},
        state=state,
        detail=f"owner wallet {age} days old with {txs} transactions",
    )


# ---------------------------------------------------------------------------
# M2 owner_multi_chain
# ---------------------------------------------------------------------------

def signal_m2_owner_multi_chain(ctx: SignalContext) -> Optional[EvidenceScore]:
    onchain = ctx.onchain or {}
    chains = onchain.get("chains_active")
    txs = onchain.get("total_tx_count") or 0
    if chains is None or txs == 0:
        return None

    if chains >= 5:
        state = "medium_human"
    elif chains >= 3:
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="M2_owner_multi_chain",
        log_lr_bits=get_lr("M2_owner_multi_chain", state),
        value={"chains_active": chains},
        state=state,
        detail=f"owner active on {chains} chains",
    )


# ---------------------------------------------------------------------------
# M3 owner_activity_score
# ---------------------------------------------------------------------------

def signal_m3_owner_activity_score(ctx: SignalContext) -> Optional[EvidenceScore]:
    onchain = ctx.onchain or {}
    txs = onchain.get("total_tx_count")
    if not txs or txs <= 0:
        return None

    score = math.log10(txs)
    if score >= 2.5:  # >= ~316 txs
        state = "medium_human"
    elif score >= 1.5:  # >= ~32 txs
        state = "weak_human"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="M3_owner_activity_score",
        log_lr_bits=get_lr("M3_owner_activity_score", state),
        value={"total_tx_count": txs, "log10_score": round(score, 2)},
        state=state,
        detail=f"owner has {txs} total transactions (log10 ≈ {score:.2f})",
    )


# ---------------------------------------------------------------------------
# M6 throwaway_owner_flag
# ---------------------------------------------------------------------------

def signal_m6_throwaway_owner_flag(ctx: SignalContext) -> Optional[EvidenceScore]:
    onchain = ctx.onchain or {}
    if not onchain:
        return None
    age = onchain.get("age_days")
    txs = onchain.get("total_tx_count") or 0

    # Dead EOA: 0 tx, possibly age None. Explicit neutral (not bot).
    if txs == 0:
        return EvidenceScore(
            signal="M6_throwaway_owner_flag",
            log_lr_bits=get_lr("M6_throwaway_owner_flag", "neutral"),
            value={"age_days": age, "total_tx_count": 0},
            state="neutral",
            detail="owner is a dead EOA (0 transactions) — not a throwaway",
        )

    if age is None:
        return None

    if age < 14 and 1 <= txs <= 10:
        state = "medium_bot"
    elif age < 30 and txs <= 20:
        state = "weak_bot"
    else:
        state = "neutral"

    return EvidenceScore(
        signal="M6_throwaway_owner_flag",
        log_lr_bits=get_lr("M6_throwaway_owner_flag", state),
        value={"age_days": age, "total_tx_count": txs},
        state=state,
        detail=f"owner {age} days old with {txs} transactions",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

ALL_ONCHAIN_SIGNALS = [
    signal_m1_owner_wallet_age,
    signal_m2_owner_multi_chain,
    signal_m3_owner_activity_score,
    signal_m6_throwaway_owner_flag,
]
