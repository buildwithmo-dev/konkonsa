from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import FeedItem
from schemas import AfricaCountryStat, FeedItemOut
from services.african_context import COUNTRIES, MIN_SCORE

router = APIRouter(prefix="/africa", tags=["Africa"])


@router.get("/summary")
async def africa_summary(hours: int = Query(24, ge=1, le=720), db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=hours)
    total = (await db.execute(
        select(func.count(FeedItem.id)).where(FeedItem.fetched_at >= since)
    )).scalar_one()
    relevant = (await db.execute(
        select(func.count(FeedItem.id)).where(
            FeedItem.fetched_at >= since, FeedItem.relevance_score >= MIN_SCORE
        )
    )).scalar_one()
    return {
        "window_hours": hours,
        "total_items": total,
        "african_items": relevant,
        "african_ratio": round(relevant / total, 3) if total else 0.0,
    }


@router.get("/countries", response_model=list[AfricaCountryStat])
async def africa_countries(days: int = Query(7, ge=1, le=90), db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(
            FeedItem.primary_country,
            func.count(FeedItem.id),
            func.avg(FeedItem.relevance_score),
            func.coalesce(func.sum(FeedItem.score), 0),
        )
        .where(
            FeedItem.fetched_at >= since,
            FeedItem.relevance_score >= MIN_SCORE,
            FeedItem.primary_country.is_not(None),
        )
        .group_by(FeedItem.primary_country)
        .order_by(desc(func.count(FeedItem.id)))
    )).all()
    return [
        AfricaCountryStat(
            code=code, name=COUNTRIES.get(code, {}).get("name", code),
            items=n, avg_relevance=round(float(avg or 0), 2), engagement=int(eng or 0),
        )
        for code, n, avg, eng in rows
    ]


@router.get("/feed", response_model=list[FeedItemOut])
async def africa_feed(
    country: Optional[str] = Query(None, min_length=2, max_length=2),
    min_score: float = Query(MIN_SCORE, ge=0, le=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(FeedItem).where(FeedItem.relevance_score >= min_score)
    if country:
        q = q.where(FeedItem.primary_country == country.upper())
    q = q.order_by(FeedItem.fetched_at.desc()).limit(limit)
    return (await db.execute(q)).scalars().all()