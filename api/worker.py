"""Dedicated Render background worker for ingestion/classification jobs."""
import asyncio
import logging
import os

from services.scheduler import seed_job_records, setup_scheduler, scheduler

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


async def main() -> None:
    await seed_job_records()
    setup_scheduler()
    scheduler.start()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
