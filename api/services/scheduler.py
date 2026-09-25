# api/services/scheduler.py
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Job, JobLog, JobStatus, Source, SourceType

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")


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


async def job_ingest_x():
    from services.x import fetch_all_configured_x_sources
    return await fetch_all_configured_x_sources()


async def job_classify_unprocessed():
    from services.classify_pipeline import classify_pending_items
    return await classify_pending_items()


async def job_synthesize_insights():
    """Roll classified feed items up into aggregated PainPoint/Trend records."""
    from services.aggregation import synthesize_insights
    return await synthesize_insights()


async def job_recluster():
    from services.clustering import recompute_clusters
    result = await recompute_clusters()
    return {"clusters": result.get("clusters", 0)}


async def job_check_alerts():
    from services.alert_checker import check_all_alerts
    return await check_all_alerts()


# ─────────────────────────────────────────────
#  Scheduler setup
# ─────────────────────────────────────────────

def setup_scheduler():
    scheduler.add_job(
        lambda: _run_job("ingest_reddit", job_ingest_reddit),
        trigger=IntervalTrigger(minutes=30), id="ingest_reddit", name="Ingest Reddit",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("ingest_hackernews", job_ingest_hn),
        trigger=IntervalTrigger(minutes=60), id="ingest_hackernews", name="Ingest Hacker News",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("ingest_google_trends", job_ingest_google_trends),
        trigger=IntervalTrigger(hours=6), id="ingest_google_trends", name="Ingest Google Trends",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("ingest_x", job_ingest_x),
        trigger=IntervalTrigger(minutes=30), id="ingest_x", name="Ingest X",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("classify_unprocessed", job_classify_unprocessed),
        trigger=IntervalTrigger(minutes=15), id="classify_unprocessed", name="Classify Unprocessed Items",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("synthesize_insights", job_synthesize_insights),
        trigger=IntervalTrigger(minutes=20), id="synthesize_insights", name="Synthesize Pain Points & Trends",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("recluster", job_recluster),
        trigger=IntervalTrigger(hours=12), id="recluster", name="Recompute Clusters",
        replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        lambda: _run_job("check_alerts", job_check_alerts),
        trigger=IntervalTrigger(minutes=10), id="check_alerts", name="Check Alerts",
        replace_existing=True, max_instances=1,
    )
    return scheduler


async def ensure_default_x_source():
    from services.x import DEFAULT_QUERY, get_x_bearer_token
    try:
        get_x_bearer_token()
    except RuntimeError:
        logger.info("X bearer token not configured; skipping default X source.")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(Source.type == SourceType.twitter, Source.name == "X")
        )
        if result.scalar_one_or_none():
            return
        db.add(Source(
            name="X", type=SourceType.twitter,
            config={"query": DEFAULT_QUERY, "max_results": 50, "max_pages": 1},
            is_active=True, fetch_interval_minutes=30,
        ))
        await db.commit()
        logger.info("Created default X source.")


async def seed_job_records():
    await ensure_default_x_source()

    job_definitions = [
        ("ingest_reddit",        "Fetch new posts from configured subreddits",           30),
        ("ingest_hackernews",    "Fetch Ask HN, Show HN, and keyword results",            60),
        ("ingest_google_trends", "Fetch trending searches and keyword interest",          360),
        ("ingest_x",             "Fetch recent X posts for configured search queries",    30),
        ("classify_unprocessed", "Classify raw feed items via the LLM",                   15),
        ("synthesize_insights",  "Roll classified items up into pain points & trends",    20),
        ("recluster",            "Re-run DBSCAN clustering on all embeddings",            720),
        ("check_alerts",         "Evaluate alert conditions and trigger notifications",   10),
    ]

    async with AsyncSessionLocal() as db:
        for name, description, interval in job_definitions:
            result = await db.execute(select(Job).where(Job.name == name))
            if not result.scalar_one_or_none():
                db.add(Job(name=name, description=description, interval_minutes=interval, status=JobStatus.idle))
        await db.commit()


async def run_all_tasks_now():
    """Manual trigger for all active scrapers, classification, and synthesis."""
    import asyncio

    logger.info("MANUAL TRIGGER: Starting all background tasks...")
    await asyncio.gather(
        _run_job("ingest_reddit", job_ingest_reddit),
        _run_job("ingest_hackernews", job_ingest_hn),
        _run_job("ingest_google_trends", job_ingest_google_trends),
        _run_job("ingest_x", job_ingest_x),
    )

    logger.info("Starting classification pipeline...")
    await _run_job("classify_unprocessed", job_classify_unprocessed)

    logger.info("Synthesizing pain points and trends...")
    await _run_job("synthesize_insights", job_synthesize_insights)

    logger.info("All manual tasks complete.")