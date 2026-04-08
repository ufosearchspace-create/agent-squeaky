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

    Produces enough day-to-day variation so T1 (sleep gap), T4 (daily volume
    CV), T5 (dead days), and T3 (weekend ratio) can all recognise the pattern.
    """
    out: list[dict] = []
    for d in range(days):
        day = _EPOCH + timedelta(days=d)
        is_weekend = day.weekday() >= 5
        hourly = trades_per_active_hour
        if is_weekend:
            hourly = max(1, int(round(trades_per_active_hour * weekend_fraction)))
        for h in active_hours:
            for i in range(hourly):
                ts = day + timedelta(hours=h, minutes=i * 15, seconds=(7 * i) % 60, milliseconds=(i * 137) % 1000)
                # Human-like variable size and hold time: odd numbers, wide variation.
                size = 87 + (i * 41) + (d * 13)
                hold_s = 1800 + (i * 311) + (d * 97)
                out.append(_to_row(ts, size=float(size), hold_s=hold_s, leverage=3 + (i % 3)))
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
    """Fast-flipping bot: sub-minute hold times, identical sizes, one coin."""
    base = _EPOCH + timedelta(days=6)
    return [
        _to_row(
            base + timedelta(minutes=i * 3),
            coin=coin,
            size=99.3742,
            leverage=10,
            hold_s=45,
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
