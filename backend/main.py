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

    # No warm start block: a previous version ran all three jobs in
    # sequence inside main() before starting the scheduler, which could
    # take 10+ minutes of wall-clock time and hid any crash from the
    # scheduler's perspective (warm start failures were only visible in
    # Railway logs). Instead, we schedule the three jobs with small
    # offsets from now() so the first execution begins shortly after
    # process start and any crash shows up in the normal scheduled run.
    now = datetime.now(tz=timezone.utc)
    scheduler = BlockingScheduler()
    scheduler.add_job(
        collector.run,
        "interval",
        minutes=30,
        id="collector",
        next_run_time=now + timedelta(seconds=10),
    )
    scheduler.add_job(
        candle_fetcher.run,
        "interval",
        minutes=30,
        id="candle_fetcher",
        next_run_time=now + timedelta(minutes=10),
    )
    scheduler.add_job(
        analyzer.run,
        "interval",
        minutes=30,
        id="analyzer",
        next_run_time=now + timedelta(minutes=20),
    )
    logger.info(
        "Scheduler started: collector @+10s, candle_fetcher @+10m, "
        "analyzer @+20m (each repeats every 30m thereafter)"
    )
    scheduler.start()


if __name__ == "__main__":
    main()
