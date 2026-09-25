from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_
from typing import Optional
from datetime import datetime, timedelta

from database import get_db
from auth import get_current_user
from models import (
    Trend, PainPoint, Solution, FeedItem, Classification,
    Cluster, Alert, AlertHistory, AlertStatus,
    Job, JobLog, JobStatus, AppSettings, Source
)
from schemas import (
    SearchResult, ClusterOut,
    AlertCreate, AlertUpdate, AlertOut, AlertHistoryOut,
    JobOut, JobLogOut,
    SummaryStats, MessageResponse, SettingUpdate,
    HealthResponse
)


# ─────────────────────────────────────────────
#  SEARCH
# ─────────────────────────────────────────────
search_router = APIRouter(prefix="/search", tags=["Search"])


@search_router.get("", response_model=list[SearchResult])
async def full_text_search(q: str = Query(..., min_length=2), db: AsyncSession = Depends(get_db)):
    results: list[SearchResult] = []
    like = f"%{q}%"

    trends = (await db.execute(
        select(Trend).where(or_(Trend.title.ilike(like), Trend.description.ilike(like))).limit(10)
    )).scalars().all()
    for t in trends:
        results.append(SearchResult(type="trend", id=t.id, title=t.title, snippet=t.description, score=t.score))

    pps = (await db.execute(
        select(PainPoint).where(or_(PainPoint.title.ilike(like), PainPoint.description.ilike(like))).limit(10)
    )).scalars().all()
    for p in pps:
        results.append(SearchResult(type="pain_point", id=p.id, title=p.title, snippet=p.description, score=p.severity))

    sols = (await db.execute(
        select(Solution).where(or_(Solution.title.ilike(like), Solution.description.ilike(like))).limit(10)
    )).scalars().all()
    for s in sols:
        results.append(SearchResult(type="solution", id=s.id, title=s.title, snippet=s.description, score=None))

    return sorted(results, key=lambda x: x.score or 0, reverse=True)


@search_router.get("/semantic", response_model=list[SearchResult])
async def semantic_search(q: str = Query(...), db: AsyncSession = Depends(get_db)):
    """
    Placeholder for semantic/embedding-based search.
    Requires sentence-transformers + Qdrant/pgvector integration.
    Falls back to full-text for now.
    """
    # TODO: embed query with sentence-transformers, query vector store
    return await full_text_search(q=q, db=db)


# ─────────────────────────────────────────────
#  CLUSTERS
# ─────────────────────────────────────────────
clusters_router = APIRouter(prefix="/clusters", tags=["Clusters"])


@clusters_router.get("", response_model=list[ClusterOut])
async def list_clusters(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Cluster).order_by(desc(Cluster.item_count)))
    return result.scalars().all()


@clusters_router.get("/{cluster_id}", response_model=dict)
async def get_cluster(cluster_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Cluster).where(Cluster.id == cluster_id))
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")

    cls_result = await db.execute(
        select(Classification).where(Classification.cluster_id == cluster_id)
    )
    items = cls_result.scalars().all()
    return {
        "cluster": ClusterOut.model_validate(cluster),
        "items": [{"id": c.id, "topic": c.topic, "summary": c.summary} for c in items],
    }


@clusters_router.post("/recompute", response_model=MessageResponse)
async def recompute_clusters(background_tasks: BackgroundTasks):
    """Triggers a background re-clustering job using DBSCAN + embeddings."""
    from services.clustering import recompute_clusters as _recompute_clusters
    background_tasks.add_task(_recompute_clusters)
    return {"message": "Cluster recomputation started in background"}


# ─────────────────────────────────────────────
#  ALERTS
# ─────────────────────────────────────────────
alerts_router = APIRouter(prefix="/alerts", tags=["Alerts"])


@alerts_router.get("", response_model=list[AlertOut])
async def list_alerts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Alert).order_by(Alert.created_at.desc()))
    return result.scalars().all()


@alerts_router.post("", response_model=AlertOut)
async def create_alert(payload: AlertCreate, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    alert = Alert(**payload.model_dump())
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


@alerts_router.put("/{alert_id}", response_model=AlertOut)
async def update_alert(alert_id: str, payload: AlertUpdate, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(alert, k, v)
    await db.commit()
    await db.refresh(alert)
    return alert


@alerts_router.delete("/{alert_id}", response_model=MessageResponse)
async def delete_alert(alert_id: str, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.delete(alert)
    await db.commit()
    return {"message": "Alert deleted"}


@alerts_router.get("/history", response_model=list[AlertHistoryOut])
async def alert_history(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertHistory).order_by(AlertHistory.triggered_at.desc()).limit(100)
    )
    return result.scalars().all()


# ─────────────────────────────────────────────
#  JOBS
#  (single definition — this router used to be declared twice in this file;
#  the second declaration silently shadowed this one at import time, so
#  GET /jobs, pause/resume/run, and /jobs/logs were never actually mounted
#  on the app. Merged into one router below.)
# ─────────────────────────────────────────────
jobs_router = APIRouter(prefix="/jobs", tags=["Jobs"])


@jobs_router.get("", response_model=list[JobOut])
async def list_jobs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).order_by(Job.name))
    return result.scalars().all()


@jobs_router.post("/run_all", response_model=MessageResponse)
async def run_all_jobs(background_tasks: BackgroundTasks, _user=Depends(get_current_user)):
    """Triggers all active scrapers, classification, and insight synthesis immediately."""
    from services.scheduler import run_all_tasks_now
    background_tasks.add_task(run_all_tasks_now)
    return {"message": "All background scrapers have been triggered manually."}


@jobs_router.post("/{job_id}/pause", response_model=JobOut)
async def pause_job(job_id: str, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = JobStatus.paused
    await db.commit()
    await db.refresh(job)
    return job


@jobs_router.post("/{job_id}/resume", response_model=JobOut)
async def resume_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = JobStatus.idle
    await db.commit()
    await db.refresh(job)
    return job


@jobs_router.post("/{job_id}/run", response_model=MessageResponse)
async def run_job(job_id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Dispatches to the actual job handler by name (previously a `pass` stub)."""
    from services.scheduler import (
        _run_job,
        job_ingest_reddit,
        job_ingest_hn,
        job_ingest_google_trends,
        job_ingest_x,
        job_classify_unprocessed,
        job_synthesize_insights,
        job_recluster,
        job_check_alerts,
    )

    handlers = {
        "ingest_reddit": job_ingest_reddit,
        "ingest_hackernews": job_ingest_hn,
        "ingest_google_trends": job_ingest_google_trends,
        "ingest_x": job_ingest_x,
        "classify_unprocessed": job_classify_unprocessed,
        "synthesize_insights": job_synthesize_insights,
        "recluster": job_recluster,
        "check_alerts": job_check_alerts,
    }

    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    handler = handlers.get(job.name)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"No handler registered for job '{job.name}'")

    background_tasks.add_task(_run_job, job.name, handler)
    return {"message": f"Job '{job.name}' triggered manually"}


@jobs_router.get("/logs", response_model=list[JobLogOut])
async def job_logs(
    job_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    query = select(JobLog).order_by(JobLog.started_at.desc()).limit(limit)
    if job_id:
        query = query.where(JobLog.job_id == job_id)
    result = await db.execute(query)
    return result.scalars().all()


# ─────────────────────────────────────────────
#  ANALYTICS
# ─────────────────────────────────────────────
analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"])


@analytics_router.get("/summary", response_model=SummaryStats)
async def summary_stats(db: AsyncSession = Depends(get_db)):
    since_24h = datetime.utcnow() - timedelta(hours=24)

    total_feed = (await db.execute(select(func.count(FeedItem.id)))).scalar_one()
    total_trends = (await db.execute(select(func.count(Trend.id)))).scalar_one()
    total_pp = (await db.execute(select(func.count(PainPoint.id)))).scalar_one()
    total_sol = (await db.execute(select(func.count(Solution.id)))).scalar_one()
    total_src = (await db.execute(select(func.count()).select_from(Source))).scalar_one()
    items_24h = (await db.execute(
        select(func.count(FeedItem.id)).where(FeedItem.fetched_at >= since_24h)
    )).scalar_one()

    return SummaryStats(
        total_feed_items=total_feed,
        total_trends=total_trends,
        total_pain_points=total_pp,
        total_solutions=total_sol,
        total_sources=total_src,
        items_last_24h=items_24h,
    )


@analytics_router.get("/sources")
async def source_stats(db: AsyncSession = Depends(get_db)):
    since_24h = datetime.utcnow() - timedelta(hours=24)
    sources = (await db.execute(select(Source))).scalars().all()
    result = []
    for src in sources:
        total = (await db.execute(
            select(func.count(FeedItem.id)).where(FeedItem.source_id == src.id)
        )).scalar_one()
        last_24h = (await db.execute(
            select(func.count(FeedItem.id))
            .where(FeedItem.source_id == src.id)
            .where(FeedItem.fetched_at >= since_24h)
        )).scalar_one()
        result.append({
            "source_id": src.id,
            "source_name": src.name,
            "total_items": total,
            "items_last_24h": last_24h,
            "error_rate": 1.0 if src.error_message else 0.0,
        })
    return result


@analytics_router.get("/categories")
async def category_breakdown(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Classification.item_type, func.count(Classification.id).label("count"))
        .group_by(Classification.item_type)
    )
    rows = result.all()
    total = sum(r.count for r in rows)
    return [
        {"category": r.item_type, "count": r.count, "percentage": round(r.count / total * 100, 2) if total else 0}
        for r in rows
    ]


@analytics_router.get("/sentiment")
async def sentiment_over_time(days: int = Query(7, ge=1, le=90), db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(
            func.date(Classification.classified_at).label("date"),
            Classification.sentiment,
            func.count(Classification.id).label("count"),
        )
        .where(Classification.classified_at >= since)
        .group_by(func.date(Classification.classified_at), Classification.sentiment)
        .order_by("date")
    )
    rows = result.all()
    grouped: dict = {}
    for r in rows:
        d = str(r.date)
        if d not in grouped:
            grouped[d] = {"date": d, "positive": 0, "negative": 0, "neutral": 0}
        if r.sentiment in grouped[d]:
            grouped[d][r.sentiment] = r.count
    return list(grouped.values())


@analytics_router.get("/heatmap")
async def activity_heatmap(db: AsyncSession = Depends(get_db)):
    """
    Returns item counts grouped by day-of-week and hour.

    NOTE: this previously used func.strftime(...), which is SQLite-only and
    would raise on the production Postgres DB. extract() is portable across
    both dialects (and is what tests already run against via aiosqlite, since
    SQLAlchemy's SQLite dialect compiles extract() down to strftime for you).
    """
    result = await db.execute(
        select(
            func.extract("dow", FeedItem.fetched_at).label("dow"),
            func.extract("hour", FeedItem.fetched_at).label("hour"),
            func.count(FeedItem.id).label("count"),
        )
        .group_by("dow", "hour")
    )
    return [{"day_of_week": int(r.dow), "hour": int(r.hour), "count": r.count} for r in result.all()]


# ─────────────────────────────────────────────
#  SETTINGS & HEALTH
# ─────────────────────────────────────────────
settings_router = APIRouter(prefix="/settings", tags=["Settings & Health"])

import time
START_TIME = time.time()


@settings_router.get("", response_model=dict)
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSettings))
    rows = result.scalars().all()
    settings = {}
    for row in rows:
        val = row.value
        if isinstance(val, str) and ("key" in row.key.lower() or "secret" in row.key.lower()):
            val = val[:4] + "****" if val else ""
        settings[row.key] = val
    return settings


@settings_router.put("/{key}", response_model=MessageResponse)
async def update_setting(key: str, payload: SettingUpdate, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = payload.value
    else:
        db.add(AppSettings(key=key, value=payload.value))
    await db.commit()
    return {"message": f"Setting '{key}' updated"}


@settings_router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(func.now()))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"

    healthy = db_status == "ok"
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version="1.1.0",
        db=db_status,
        uptime_seconds=round(time.time() - START_TIME, 2),
    )


@settings_router.get("/version")
async def version():
    return {"version": "1.0.0", "model": "groq"}