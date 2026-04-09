"""Main entry point: APScheduler runs collector, candle_fetcher, and
analyzer on a 30-minute schedule. candle_fetcher is scheduled between
collector and analyzer so every score row sees fresh candles."""
import logging
import sys

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

    scheduler = BlockingScheduler()
    scheduler.add_job(collector.run, "interval", minutes=30, id="collector")
    scheduler.add_job(candle_fetcher.run, "interval", minutes=30, id="candle_fetcher")
    scheduler.add_job(analyzer.run, "interval", minutes=30, id="analyzer")
    logger.info("Scheduler started: collector=30m, candle_fetcher=30m, analyzer=30m")
    scheduler.start()


if __name__ == "__main__":
    main()
