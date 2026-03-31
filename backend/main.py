"""
Main entry point: APScheduler runs collector, analyzer, reporter on schedule.
"""

import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

import collector
import analyzer
import reporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def analysis_cycle():
    """Run analyzer then reporter."""
    analyzer.run()
    reporter.post_analysis_report()


def main():
    logger.info("=== Agent Squeaky Scanner starting ===")

    # Run initial collection immediately
    try:
        collector.run()
    except Exception:
        logger.exception("Initial collection failed")

    # Run initial analysis
    try:
        analysis_cycle()
    except Exception:
        logger.exception("Initial analysis failed")

    scheduler = BlockingScheduler()
    scheduler.add_job(collector.run, "interval", minutes=30, id="collector")
    scheduler.add_job(analysis_cycle, "interval", minutes=30, id="analyzer")
    scheduler.add_job(reporter.daily_summary, "cron", hour=8, minute=0, id="daily_summary")

    logger.info("Scheduler started: collector=30m, analyzer=30m, daily_summary=08:00 UTC")
    scheduler.start()


if __name__ == "__main__":
    main()
