from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional

from database import get_db
from models import PainPoint
from schemas import PainPointOut, PainPointUpdate, MessageResponse

router = APIRouter(prefix="/painpoints", tags=["Pain Points"])


@router.get("", response_model=list[PainPointOut])
async def list_pain_points(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_dismissed: bool = False,
    db: AsyncSession = Depends(get_db),
):
    query = select(PainPoint).order_by(desc(PainPoint.severity))
    if not include_dismissed:
        query = query.where(PainPoint.is_dismissed == False)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/top", response_model=list[PainPointOut])
async def top_pain_points(
    n: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PainPoint)
        .where(PainPoint.is_dismissed == False)
        .order_by(desc(PainPoint.severity))
        .limit(n)
    )
    return result.scalars().all()


@router.get("/by-audience", response_model=list[dict])
async def pain_points_by_audience(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    result = await db.execute(
        select(PainPoint.audience, func.count(PainPoint.id).label("count"))
        .where(PainPoint.is_dismissed == False)
        .group_by(PainPoint.audience)
        .order_by(desc("count"))
    )
    return [{"audience": r.audience or "Unknown", "count": r.count} for r in result.all()]


@router.get("/{pain_point_id}", response_model=PainPointOut)
async def get_pain_point(pain_point_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PainPoint).where(PainPoint.id == pain_point_id))
    pp = result.scalar_one_or_none()
    if not pp:
        raise HTTPException(status_code=404, detail="Pain point not found")
    return pp


@router.put("/{pain_point_id}/severity", response_model=PainPointOut)
async def update_severity(
    pain_point_id: str,
    payload: PainPointUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PainPoint).where(PainPoint.id == pain_point_id))
    pp = result.scalar_one_or_none()
    if not pp:
        raise HTTPException(status_code=404, detail="Pain point not found")
    if payload.severity is not None:
        pp.severity = payload.severity
    await db.commit()
    await db.refresh(pp)
    return pp


@router.post("/{pain_point_id}/dismiss", response_model=MessageResponse)
async def dismiss_pain_point(pain_point_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PainPoint).where(PainPoint.id == pain_point_id))
    pp = result.scalar_one_or_none()
    if not pp:
        raise HTTPException(status_code=404, detail="Pain point not found")
    pp.is_dismissed = True
    await db.commit()
    return {"message": "Pain point dismissed"}
