"""Main entry point: APScheduler runs collector, candle_fetcher, and
analyzer on a 30-minute schedule. The three jobs share the same
interval but are staggered via ``next_run_time`` so they never fire
concurrently — the intended order is collector -> candle_fetcher ->
analyzer, each separated by enough margin that the previous job has
finished when the next starts."""
import logging
import sys
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

import analyzer
import candle_fetcher
import collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("=== Agent Squeaky Scanner starting ===")

    # Warm start: one full pass before the scheduler takes over. Order
    # matters — candles must exist before analyzer runs B4/B4b.
    try:
        collector.run()
    except Exception:
        logger.exception("Initial collection failed")
    try:
        candle_fetcher.run()
    except Exception:
        logger.exception("Initial candle fetch failed")
    try:
        analyzer.run()
    except Exception:
        logger.exception("Initial analysis failed")

    # Stagger the scheduled ticks so the three jobs run in sequence and
    # never fight each other for the Supabase connection or for the
    # DegenClaw / Hyperliquid rate limits. The first tick happens 30
    # minutes after the warm start for collector, then +10 and +20
    # minutes for candle_fetcher and analyzer respectively.
    now = datetime.now(tz=timezone.utc)
    scheduler = BlockingScheduler()
    scheduler.add_job(
        collector.run,
        "interval",
        minutes=30,
        id="collector",
        next_run_time=now + timedelta(minutes=30),
    )
    scheduler.add_job(
        candle_fetcher.run,
        "interval",
        minutes=30,
        id="candle_fetcher",
        next_run_time=now + timedelta(minutes=40),
    )
    scheduler.add_job(
        analyzer.run,
        "interval",
        minutes=30,
        id="analyzer",
        next_run_time=now + timedelta(minutes=50),
    )
    logger.info(
        "Scheduler started: collector @+30m, candle_fetcher @+40m, "
        "analyzer @+50m (each repeats every 30m thereafter)"
    )
    scheduler.start()


if __name__ == "__main__":
    main()
