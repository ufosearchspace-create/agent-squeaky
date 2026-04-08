"""Main entry point: APScheduler runs collector + analyzer on schedule."""
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

import analyzer
import collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("=== Agent Squeaky Scanner starting ===")

    # Warm start: one collection + analysis cycle before the scheduler takes over.
    try:
        collector.run()
    except Exception:
        logger.exception("Initial collection failed")
    try:
        analyzer.run()
    except Exception:
        logger.exception("Initial analysis failed")

    scheduler = BlockingScheduler()
    scheduler.add_job(collector.run, "interval", minutes=30, id="collector")
    scheduler.add_job(analyzer.run, "interval", minutes=30, id="analyzer")
    logger.info("Scheduler started: collector=30m, analyzer=30m")
    scheduler.start()


if __name__ == "__main__":
    main()
