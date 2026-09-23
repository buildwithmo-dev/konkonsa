import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Job, JobLog, JobStatus

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")


# ─────────────────────────────────────────────
#  Job wrappers — each logs to the DB
# ─────────────────────────────────────────────

async def _run_job(job_name: str, fn):
    """Wrapper that records start/end and errors in JobLog."""
    started = datetime.utcnow()
    log = JobLog(started_at=started, items_processed=0, success=True)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.name == job_name))
        job = result.scalar_one_or_none()
        if job:
            job.status = JobStatus.running
            log.job_id = job.id
            db.add(log)
            await db.commit()
            await db.refresh(log)

    try:
        result = await fn()
        count = sum(result.values()) if isinstance(result, dict) else (result or 0)
        log.items_processed = count
        log.success = True
        log.message = f"OK — {count} items"
        logger.info(f"[{job_name}] completed — {count} items")
    except Exception as e:
        log.success = False
        log.message = str(e)
        logger.error(f"[{job_name}] failed: {e}")
    finally:
        log.finished_at = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.name == job_name))
        job = result.scalar_one_or_none()
        if job:
            job.status = JobStatus.idle
            job.last_run_at = started
            job.run_count += 1
            job.last_error = log.message if not log.success else None

        log_result = await db.execute(select(JobLog).where(JobLog.id == log.id))
        log_obj = log_result.scalar_one_or_none()
        if log_obj:
            log_obj.success = log.success
            log_obj.message = log.message
            log_obj.finished_at = log.finished_at
            log_obj.items_processed = log.items_processed

        await db.commit()


# ─────────────────────────────────────────────
#  Individual job functions
# ─────────────────────────────────────────────

async def job_ingest_reddit():
    from services.reddit import fetch_all_configured_subreddits
    return await fetch_all_configured_subreddits()


async def job_ingest_hn():
    from services.hackernews import fetch_all_configured_hn_sources
    return await fetch_all_configured_hn_sources()


async def job_ingest_google_trends():
    from services.google_trends import fetch_all_configured_trends_sources
    return await fetch_all_configured_trends_sources()


async def job_classify_unprocessed():
    """Classify all feed items that don't have a classification yet."""
    from services.classify_pipeline import classify_pending_items
    return await classify_pending_items()


async def job_recluster():
    """Re-run DBSCAN clustering on all classified items."""
    from services.clustering import recompute_clusters
    result = await recompute_clusters()
    return {"clusters": result.get("clusters", 0)}


async def job_check_alerts():
    """Evaluate all active alerts and trigger if conditions are met."""
    from services.alert_checker import check_all_alerts
    return await check_all_alerts()


# ─────────────────────────────────────────────
#  Scheduler setup
# ─────────────────────────────────────────────

def setup_scheduler():
    """Register all jobs with their intervals. Call once at startup."""

    scheduler.add_job(
        lambda: _run_job("ingest_reddit", job_ingest_reddit),
        trigger=IntervalTrigger(minutes=30),
        id="ingest_reddit",
        name="Ingest Reddit",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        lambda: _run_job("ingest_hackernews", job_ingest_hn),
        trigger=IntervalTrigger(minutes=60),
        id="ingest_hackernews",
        name="Ingest Hacker News",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        lambda: _run_job("ingest_google_trends", job_ingest_google_trends),
        trigger=IntervalTrigger(hours=6),
        id="ingest_google_trends",
        name="Ingest Google Trends",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        lambda: _run_job("classify_unprocessed", job_classify_unprocessed),
        trigger=IntervalTrigger(minutes=15),
        id="classify_unprocessed",
        name="Classify Unprocessed Items",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        lambda: _run_job("recluster", job_recluster),
        trigger=IntervalTrigger(hours=12),
        id="recluster",
        name="Recompute Clusters",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        lambda: _run_job("check_alerts", job_check_alerts),
        trigger=IntervalTrigger(minutes=10),
        id="check_alerts",
        name="Check Alerts",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler


async def seed_job_records():
    """Ensure Job rows exist in the DB for all registered jobs."""
    job_definitions = [
        ("ingest_reddit",       "Fetch new posts from configured subreddits",        30),
        ("ingest_hackernews",   "Fetch Ask HN, Show HN, and keyword results",         60),
        ("ingest_google_trends","Fetch trending searches and keyword interest",        360),
        ("classify_unprocessed","Classify raw feed items via Claude API",              15),
        ("recluster",           "Re-run DBSCAN clustering on all embeddings",          720),
        ("check_alerts",        "Evaluate alert conditions and trigger notifications", 10),
    ]

    async with AsyncSessionLocal() as db:
        for name, description, interval in job_definitions:
            result = await db.execute(select(Job).where(Job.name == name))
            if not result.scalar_one_or_none():
                db.add(Job(
                    name=name,
                    description=description,
                    interval_minutes=interval,
                    status=JobStatus.idle,
                ))
        await db.commit()

async def run_all_tasks_now():
    """Manual trigger for all active scrapers and classification pipeline."""
    import asyncio
    print("🚀 MANUAL TRIGGER: Starting all background tasks...")
    
    # Run ingestion jobs concurrently
    await asyncio.gather(
        _run_job("ingest_reddit", job_ingest_reddit),
        _run_job("ingest_hackernews", job_ingest_hn),
        _run_job("ingest_google_trends", job_ingest_google_trends)
    )
    
    # Run classification after ingestion finishes
    print("🧠 Starting classification pipeline...")
    await _run_job("classify_unprocessed", job_classify_unprocessed)
    
    print("✅ All manual tasks complete.")