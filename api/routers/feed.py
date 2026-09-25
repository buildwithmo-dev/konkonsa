from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import asyncio

from database import get_db
from models import FeedItem
from schemas import FeedItemOut, MessageResponse
from services.realtime import manager

router = APIRouter(prefix="/feed", tags=["Feed"])


@router.get("", response_model=list[FeedItemOut])
async def list_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    limit: Optional[int] = Query(
        None, ge=1, le=200,
        description="Alias for page_size — kept for frontend compatibility (lib/api.ts calls ?limit=50)",
    ),
    source_id: Optional[str] = None,
    is_duplicate: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    effective_page_size = limit or page_size
    query = select(FeedItem).order_by(FeedItem.fetched_at.desc())
    if source_id:
        query = query.where(FeedItem.source_id == source_id)
    if is_duplicate is not None:
        query = query.where(FeedItem.is_duplicate == is_duplicate)
    query = query.offset((page - 1) * effective_page_size).limit(effective_page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/duplicates", response_model=list[FeedItemOut])
async def list_duplicates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedItem).where(FeedItem.is_duplicate == True))
    return result.scalars().all()


@router.get("/{item_id}", response_model=FeedItemOut)
async def get_feed_item(item_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Feed item not found")
    return item


@router.delete("/{item_id}", response_model=MessageResponse)
async def delete_feed_item(item_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Feed item not found")
    await db.delete(item)
    await db.commit()
    return {"message": "Feed item removed"}


@router.websocket("/ws/feed")
async def websocket_feed(websocket: WebSocket):
    """
    Streams a message whenever any ingestion service (running on the worker
    dyno) commits new FeedItems — see services/realtime.py for how that
    crosses the process boundary via Postgres NOTIFY.
    """
    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)