from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime

from database import get_db
from auth import get_current_user
from models import Source
from schemas import SourceCreate, SourceUpdate, SourceOut, MessageResponse
# Add this import at the top if it's not there
from services.hackernews import search_hn 

router = APIRouter(prefix="/sources", tags=["Sources"])

@router.get("", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).order_by(Source.created_at.desc()))
    return result.scalars().all()

@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(payload: SourceCreate, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    source = Source(**payload.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source

@router.put("/{source_id}", response_model=SourceOut)
async def update_source(source_id: str, payload: SourceUpdate, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = payload.model_dump(exclude_none=True)
    
    if update_data:
        await db.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(**update_data)
        )
        await db.commit()
        await db.refresh(source)

    return source

@router.delete("/{source_id}", response_model=MessageResponse)
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    await db.commit()
    return {"message": "Source deleted"}



@router.post("/{source_id}/trigger", response_model=MessageResponse)
async def trigger_source(source_id: str, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # This is the magic part: Calling the actual "Scout" code
    if source.type == "hackernews":
        config = source.config or {}
        # We run the actual search logic here
        count = await search_hn(
            query=config.get("query", ""),
            source_id=source.id,
            tags=config.get("tags", "story")
        )
        return {"message": f"Success! Fetched {count} items for '{source.name}'"}
    
    return {"message": "Source triggered, but no worker assigned for this type."}