"""Dedicated Render background worker for ingestion/classification jobs."""
import asyncio
import logging
import os

from services.scheduler import seed_default_sources, seed_job_records, setup_scheduler, scheduler

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


async def main() -> None:
    await seed_job_records()
    await seed_default_sources()
    setup_scheduler()
    scheduler.start()
    # Populate the dashboard immediately instead of waiting for the first interval.
    from services.scheduler import run_all_tasks_now
    await run_all_tasks_now()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
