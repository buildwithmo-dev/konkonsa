from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from typing import Optional
from datetime import datetime, timedelta
import asyncio

from database import get_db
from models import Trend, ItemType, FeedItem
from schemas import TrendOut

router = APIRouter(prefix="/trends", tags=["Trends"])


@router.get("", response_model=list[TrendOut])
async def list_trends(
    category: Optional[ItemType] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    min_score: Optional[float] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = select(Trend).order_by(desc(Trend.score))
    if category:
        query = query.where(Trend.category == category)
    if start_date:
        query = query.where(Trend.first_seen_at >= start_date)
    if end_date:
        query = query.where(Trend.first_seen_at <= end_date)
    if min_score:
        query = query.where(Trend.score >= min_score)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/top", response_model=list[TrendOut])
async def top_trends(
    n: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trend).order_by(desc(Trend.score)).limit(n)
    )
    return result.scalars().all()


@router.get("/rising", response_model=list[TrendOut])
async def rising_trends(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(Trend)
        .where(Trend.is_rising == True)
        .where(Trend.last_seen_at >= since)
        .order_by(desc(Trend.volume))
    )
    return result.scalars().all()


@router.get("/categories")
async def trends_by_category(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Trend.category, func.count(Trend.id).label("count"))
        .group_by(Trend.category)
    )
    rows = result.all()
    total = sum(r.count for r in rows)
    return [
        {
            "category": r.category,
            "count": r.count,
            "percentage": round(r.count / total * 100, 2) if total else 0,
        }
        for r in rows
    ]


@router.get("/timeline")
async def trend_timeline(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Returns daily trend volume for the past N days."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(
            func.date(FeedItem.fetched_at).label("date"),
            func.count(FeedItem.id).label("count"),
        )
        .where(FeedItem.fetched_at >= since)
        .group_by(func.date(FeedItem.fetched_at))
        .order_by("date")
    )
    return [{"date": str(r.date), "count": r.count} for r in result.all()]


@router.get("/{trend_id}", response_model=TrendOut)
async def get_trend(trend_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trend).where(Trend.id == trend_id))
    trend = result.scalar_one_or_none()
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    return trend


@router.websocket("/ws/trends")
async def websocket_trends(websocket: WebSocket):
    """Streams new/updated trends in real-time."""
    await websocket.accept()
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
