"""Synthetic trade-timeline generators used by signal + gate tests.

All helpers return a list of dicts shaped like rows from scanner_trades.
``closed_at_ms`` always populated; ``opened_at_ms`` defaults to 60s before
close so the GENERATED hold_time_s column would be 60 for most helpers.

Helpers are intentionally low-level and configurable so tests can build
the exact pattern they need (long sleep, Martingale ladder, bimodal
scalper, etc.).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_EPOCH = datetime(2026, 3, 25, 0, 0, 0, tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _to_row(
    ts: datetime,
    *,
    coin: str = "BTC",
    size: float = 100.0,
    leverage: int = 5,
    pnl: float = 0.5,
    direction: str = "LONG",
    hold_s: int = 60,
    entry_price: float = 70000.0,
    exit_price: float = 70100.0,
) -> dict:
    closed_ms = _ms(ts)
    opened_ms = closed_ms - hold_s * 1000
    return {
        "dgc_trade_id": f"synth-{closed_ms}",
        "opened_at_ms": opened_ms,
        "closed_at_ms": closed_ms,
        "hold_time_s": hold_s,
        "coin": coin,
        "direction": direction,
        "position_size": size,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "leverage": leverage,
        "closed_pnl": pnl,
    }


def human_daily_trader(
    days: int = 10,
    trades_per_active_hour: int = 2,
    active_hours: tuple[int, ...] = (9, 10, 11, 14, 15, 16, 19, 20),
    weekend_fraction: float = 0.4,
) -> list[dict]:
    """Simulate a human: ~8h active window, variable sizes, weekend lighter.

    Produces enough day-to-day variation so T1 (sleep gap), T3 (weekend
    ratio), T4 (daily volume CV), and T5 (dead days) can all recognise the
    pattern. Each day randomly picks a subset of active hours and varies
    trades/hour so the CV is realistically human.
    """
    out: list[dict] = []
    # Deterministic "mood" offsets per day — mimics human day-to-day variance
    # without introducing real randomness (tests must stay reproducible).
    day_moods = [
        # (hours_subset_offset, trades_per_hour_multiplier)
        (0, 1.0), (2, 0.5), (1, 1.5), (3, 0.7), (0, 1.2),
        (4, 0.3), (2, 1.3), (1, 0.9), (0, 0.6), (3, 1.4),
        (2, 0.8), (0, 1.1), (1, 0.4), (4, 1.0),
    ]
    for d in range(days):
        day = _EPOCH + timedelta(days=d)
        is_weekend = day.weekday() >= 5
        mood_offset, mood_mult = day_moods[d % len(day_moods)]
        day_hours = active_hours[mood_offset:] or active_hours
        hourly = max(1, int(round(trades_per_active_hour * mood_mult)))
        if is_weekend:
            hourly = max(1, int(round(hourly * weekend_fraction)))
        # Human hold times span orders of magnitude: some quick scalps,
        # some multi-hour swings, some held over lunch. Deterministic
        # sequence so tests stay reproducible.
        hold_palette = [45, 180, 720, 1800, 5400, 14400, 43200, 120]
        for h in day_hours:
            for i in range(hourly):
                # Wide ms spread per trade so T8 entropy stays high.
                ms_spread = (i * 571 + d * 89 + h * 257) % 1000
                ts = day + timedelta(
                    hours=h,
                    minutes=i * 15 + (d * 7) % 60,
                    seconds=(7 * i + 11 * d) % 60,
                    milliseconds=ms_spread,
                )
                size = 87 + (i * 41) + (d * 13) + (h * 3)
                hold_s = hold_palette[(i + d * 3 + h) % len(hold_palette)]
                out.append(_to_row(ts, size=float(size), hold_s=hold_s, leverage=3 + ((i + d) % 3)))
    return out


def bot_24_7(
    days: int = 10,
    trades_per_hour: int = 5,
    size: float = 99.22625500000001,
    leverage: int = 5,
) -> list[dict]:
    """Simulate a relentless 24/7 bot: same size, same leverage, every hour.

    Strong T1 (no sleep), T4 (low daily CV), T8 (possible millisecond zeros),
    S2 (many decimals), S6 (identical sizes), S7 (single leverage).
    """
    out: list[dict] = []
    for d in range(days):
        for h in range(24):
            for i in range(trades_per_hour):
                minute = i * (60 // trades_per_hour)
                ts = _EPOCH + timedelta(days=d, hours=h, minutes=minute)
                out.append(_to_row(ts, size=size, leverage=leverage, hold_s=300))
    return out


def bot_24_7_zero_ms(days: int = 10, trades_per_hour: int = 4) -> list[dict]:
    """Bot whose trade timestamps all land on exact seconds (ms == 0)."""
    out: list[dict] = []
    for d in range(days):
        for h in range(24):
            for i in range(trades_per_hour):
                ts = _EPOCH + timedelta(days=d, hours=h, minutes=i * (60 // trades_per_hour))
                out.append(_to_row(ts, size=99.22, leverage=5, hold_s=300))
    return out


def scalper_bot(n: int = 200, coin: str = "SOL") -> list[dict]:
    """Fast-flipping bot: sub-minute hold times, tight spacing, one coin."""
    base = _EPOCH + timedelta(days=6)
    # 45-second spacing keeps several trades inside the 5-minute T6 window.
    return [
        _to_row(
            base + timedelta(seconds=i * 45),
            coin=coin,
            size=99.3742,
            leverage=10,
            hold_s=30,
        )
        for i in range(n)
    ]


def multi_coin_rapid_bot(
    days: int = 7,
    trades_per_day: int = 40,
    coins: tuple[str, ...] = (
        "BTC", "ETH", "SOL", "HYPE", "VIRTUAL", "SUI", "AVAX", "ARB",
        "OP", "MATIC", "NEAR", "INJ", "SEI", "TIA", "DOGE", "SHIB",
    ),
) -> list[dict]:
    """Bot spamming many coins rapidly — triggers S4 (coin diversity)."""
    out: list[dict] = []
    for d in range(days):
        for i in range(trades_per_day):
            coin = coins[(d * trades_per_day + i) % len(coins)]
            ts = _EPOCH + timedelta(days=d, minutes=i * 30)
            out.append(_to_row(ts, coin=coin, size=99.1 + (i % 3) * 0.3, leverage=5, hold_s=120))
    return out


def ladder_bot(n: int = 60, bases: tuple[float, ...] = (50.0, 100.0, 200.0)) -> list[dict]:
    """Bot whose position sizes fall into exactly three fixed buckets."""
    out: list[dict] = []
    for i in range(n):
        ts = _EPOCH + timedelta(days=i // 24, hours=i % 24)
        out.append(_to_row(ts, size=bases[i % len(bases)], leverage=5, hold_s=600))
    return out


def bimodal_hold_bot(n: int = 120) -> list[dict]:
    """Bot whose hold times cluster tightly around two values (bimodal)."""
    out: list[dict] = []
    for i in range(n):
        ts = _EPOCH + timedelta(hours=i)
        hold_s = 60 if i % 10 != 0 else 3600
        out.append(_to_row(ts, size=99.22, leverage=5, hold_s=hold_s))
    return out


def asymmetric_win_loss_human(n_wins: int = 15, n_losses: int = 15) -> list[dict]:
    """Human: holds losers 4x longer than winners (emotional bias)."""
    out: list[dict] = []
    for i in range(n_wins):
        ts = _EPOCH + timedelta(hours=i * 2)
        out.append(_to_row(ts, size=float(50 + 7 * i), hold_s=900, pnl=2.5))
    for i in range(n_losses):
        ts = _EPOCH + timedelta(hours=n_wins * 2 + i * 3)
        out.append(_to_row(ts, size=float(63 + 11 * i), hold_s=3600, pnl=-3.1))
    return out


def multi_agent_concurrent_bot(days: int = 5, positions_open_at_once: int = 8) -> list[dict]:
    """Bot that always has N overlapping positions open (triggers B5)."""
    out: list[dict] = []
    for d in range(days):
        for p in range(positions_open_at_once):
            for cycle in range(4):
                ts = _EPOCH + timedelta(days=d, hours=cycle * 6, minutes=p * 5)
                # Long hold ensures overlap with sibling positions.
                out.append(_to_row(ts, coin=f"C{p}", size=99.0 + p * 0.1, hold_s=18000))
    return out


# ---------------------------------------------------------------------------
# PR4 psychology fixtures — hybrid-agent patterns for B6..B10, S8, S9, T9.
# ---------------------------------------------------------------------------

def loss_chase_human(n_cycles: int = 10) -> list[dict]:
    """Sequence where position size grows after losing streaks.

    Generates alternating loss-streak / win-streak cycles. After each
    loss streak, position size roughly doubles (chase). This creates a
    negative Pearson r between rolling 5-trade PnL and next size — the
    classic loss-chase signature B7 targets.
    """
    out: list[dict] = []
    ts_cursor = _EPOCH
    base_size = 50.0
    for cycle in range(n_cycles):
        # 5 losing trades at a baseline size.
        for i in range(5):
            out.append(
                _to_row(
                    ts_cursor,
                    size=base_size,
                    hold_s=900,
                    pnl=-4.0 - i * 0.3,
                )
            )
            ts_cursor += timedelta(hours=2)
        # "Chase" escalation: next 3 trades at 2x-3x size.
        base_size *= 2.1
        for i in range(3):
            out.append(
                _to_row(
                    ts_cursor,
                    size=base_size,
                    hold_s=1200,
                    pnl=(i - 1) * 2.0,
                )
            )
            ts_cursor += timedelta(hours=3)
        # Reset size after the chase — human "calms down".
        base_size = 50.0
    return out


def hot_hand_human(n_cycles: int = 8) -> list[dict]:
    """Sequence where trade pace accelerates after win streaks.

    In each cycle: 5 winning trades, then a burst of 5 rapid trades
    (minutes apart), then 5 losing trades, then a pause. Produces a
    positive correlation between trailing win rate and next-window pace.
    """
    out: list[dict] = []
    ts = _EPOCH
    for cycle in range(n_cycles):
        # 5 wins, 2h apart.
        for i in range(5):
            out.append(_to_row(ts, size=100.0 + i, hold_s=1800, pnl=2.5))
            ts += timedelta(hours=2)
        # HOT HAND burst: 5 trades, 3 minutes apart.
        for i in range(5):
            out.append(_to_row(ts, size=120.0 + i, hold_s=600, pnl=1.5))
            ts += timedelta(minutes=3)
        # 5 losses, 4h apart (slowed pace after streak breaks).
        for i in range(5):
            out.append(_to_row(ts, size=90.0 + i, hold_s=3600, pnl=-2.0))
            ts += timedelta(hours=4)
    return out


def tilt_spike_human(n_cycles: int = 6) -> list[dict]:
    """Pattern where big losses trigger 30-min bursts of revenge trades.

    Baseline: one trade every 2 hours. After each big loss (>>1σ), 8
    trades in a 20-minute window. This triggers the B9 post-big-loss
    rate ratio well above 2.0x.
    """
    out: list[dict] = []
    ts = _EPOCH
    # Baseline filler: 20 normal trades, small PnL noise.
    for i in range(20):
        out.append(_to_row(ts, size=100.0, hold_s=1500, pnl=0.5 if i % 2 else -0.5))
        ts += timedelta(hours=2)
    # Big loss + revenge cycle.
    for cycle in range(n_cycles):
        out.append(_to_row(ts, size=500.0, hold_s=600, pnl=-50.0))
        ts += timedelta(minutes=2)
        # Revenge burst: 8 trades in 20 minutes.
        for i in range(8):
            out.append(
                _to_row(
                    ts,
                    size=200.0 + i * 10,
                    hold_s=300,
                    pnl=(-1.0 if i % 2 else 1.0),
                )
            )
            ts += timedelta(minutes=2)
        # Pause back to baseline tempo.
        for i in range(5):
            ts += timedelta(hours=3)
            out.append(_to_row(ts, size=100.0, hold_s=1500, pnl=0.3))
    return out


def round_pnl_exits_human(n: int = 40) -> list[dict]:
    """Agent whose exits land on round PnL values (+100, -50, +250, etc.)."""
    out: list[dict] = []
    targets = [100.0, 50.0, -50.0, 250.0, -100.0, 500.0, -25.0, 100.0, 50.0, -250.0]
    for i in range(n):
        ts = _EPOCH + timedelta(hours=i * 2)
        target = targets[i % len(targets)]
        # Tight noise around round PnL: within 2% — within signal's 5% band.
        pnl = target * (1 + 0.01 * ((i % 3) - 1))
        out.append(_to_row(ts, size=100.0 + i, hold_s=1500, pnl=pnl))
    return out


def anchor_exits_human(n: int = 40) -> list[dict]:
    """Agent whose exits target round percent returns (+1%, +2%, -5%, etc.).

    Each trade enters at 1000 and exits at a price giving one of the
    round anchor returns. Triggers S9 anchor_exits.
    """
    out: list[dict] = []
    anchors = [0.01, 0.02, 0.05, -0.02, 0.10, -0.05, 0.025, 0.075]
    for i in range(n):
        ts = _EPOCH + timedelta(hours=i * 2)
        ret = anchors[i % len(anchors)]
        entry = 1000.0
        exit_ = entry * (1 + ret)
        out.append(
            {
                "dgc_trade_id": f"synth-anchor-{i}",
                "opened_at_ms": _ms(ts) - 600_000,
                "closed_at_ms": _ms(ts),
                "hold_time_s": 600,
                "coin": "BTC",
                "direction": "LONG",
                "position_size": 100.0 + i,
                "entry_price": entry,
                "exit_price": exit_,
                "leverage": 5,
                "closed_pnl": ret * 100 + (i * 0.17),  # noisy PnL, NOT round
            }
        )
    return out


def circadian_gaps_human(days: int = 10) -> list[dict]:
    """Trader with tight circadian gap pattern — 6-8 trades during a
    daily active window, sleep gap every night at the same UTC hour.

    Produces low gap-start entropy (most gaps begin near 21:00 UTC).
    """
    out: list[dict] = []
    for d in range(days):
        day = _EPOCH + timedelta(days=d)
        # Active window 13-21 UTC, 2 trades per hour.
        for h in range(13, 21):
            for i in range(2):
                ts = day + timedelta(hours=h, minutes=i * 30, seconds=(d * 7 + h) % 60)
                out.append(_to_row(ts, size=100.0 + i + d, hold_s=1200))
    return out


def u_shape_retail_human(days: int = 10) -> list[dict]:
    """Retail human: U-shape hourly distribution (crypto US market open +
    late-evening emotional close-outs).

    Concentrates trades at 13-15 UTC (US market open) and 20-22 UTC
    (late evening), with a mid-day lull between them.
    """
    out: list[dict] = []
    for d in range(days):
        day = _EPOCH + timedelta(days=d)
        # Morning burst: 6 trades between 13:00 and 15:00.
        for i in range(6):
            ts = day + timedelta(hours=13, minutes=i * 20)
            out.append(_to_row(ts, size=100.0 + i, hold_s=900))
        # Mid-day lull: 1 trade at 17:00.
        out.append(_to_row(day + timedelta(hours=17), size=90.0, hold_s=3000))
        # Evening burst: 6 trades between 20:00 and 22:00.
        for i in range(6):
            ts = day + timedelta(hours=20, minutes=i * 20)
            out.append(_to_row(ts, size=110.0 + i, hold_s=600))
    return out


def hybrid_bot_with_human_touch(days: int = 12) -> list[dict]:
    """A 24/7 bot with ~10% of trades showing human-psychology markers.

    Used to test the HUMAN_ASSISTED classifier: most signals lean bot,
    but at least two psychology signals should still trip because the
    human intervention trades are distinct enough to push B6 + S8
    (or B9 + S9, etc.) into human-leaning states.
    """
    out: list[dict] = []
    # 90% bot backbone: constant size, uniform spacing, constant hold.
    for d in range(days):
        for h in range(24):
            for i in range(3):
                ts = _EPOCH + timedelta(days=d, hours=h, minutes=i * 20)
                out.append(_to_row(ts, size=99.22, hold_s=300, pnl=0.4))
    # 10% human-touch overlay: long holds on losers (disposition) + round
    # PnL exits (S8) + one revenge burst.
    for d in range(days):
        # One losing trade held for 6h.
        ts = _EPOCH + timedelta(days=d, hours=22, minutes=5)
        out.append(_to_row(ts, size=150.0, hold_s=21600, pnl=-25.0))
        # Round-PnL exit.
        ts = _EPOCH + timedelta(days=d, hours=11, minutes=17)
        out.append(_to_row(ts, size=150.0, hold_s=1800, pnl=100.0))
    # Add a revenge burst after one of the big losses.
    big_loss_ts = _EPOCH + timedelta(days=5, hours=22, minutes=5)
    for i in range(6):
        out.append(
            _to_row(
                big_loss_ts + timedelta(minutes=3 + i * 2),
                size=200.0,
                hold_s=120,
                pnl=(-2.0 if i % 2 else 1.5),
            )
        )
    return out
