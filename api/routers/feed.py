from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional
import asyncio, json

from database import get_db
from models import FeedItem
from schemas import FeedItemOut, MessageResponse

router = APIRouter(prefix="/feed", tags=["Feed"])

# Simple in-memory connection manager for WebSocket clients
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                pass

manager = ConnectionManager()


@router.get("", response_model=list[FeedItemOut])
async def list_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_id: Optional[str] = None,
    is_duplicate: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(FeedItem).order_by(FeedItem.fetched_at.desc())
    if source_id:
        query = query.where(FeedItem.source_id == source_id)
    if is_duplicate is not None:
        query = query.where(FeedItem.is_duplicate == is_duplicate)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/duplicates", response_model=list[FeedItemOut])
async def list_duplicates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FeedItem).where(FeedItem.is_duplicate == True)
    )
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
    WebSocket endpoint — streams new feed items in real-time.
    Connect and receive JSON payloads whenever new items are ingested.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; broadcasting is triggered by ingestion service
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# Utility: call this from the ingestion service to push new items to WS clients
async def broadcast_new_item(item: dict):
    await manager.broadcast({"type": "new_item", "data": item})
