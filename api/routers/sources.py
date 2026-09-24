from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models import Source
from schemas import MessageResponse, SourceCreate, SourceOut, SourceUpdate
from services.hackernews import search_hn
from services.x import search_x

router = APIRouter(prefix="/sources", tags=["Sources"])


@router.get("", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).order_by(Source.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    source = Source(**payload.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.put("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: str,
    payload: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = payload.model_dump(exclude_none=True)
    if update_data:
        await db.execute(
            update(Source).where(Source.id == source_id).values(**update_data)
        )
        await db.commit()
        await db.refresh(source)

    return source


@router.delete("/{source_id}", response_model=MessageResponse)
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    await db.commit()
    return {"message": "Source deleted"}


@router.get("/{source_id}/status", response_model=SourceOut)
async def source_status(source_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post("/{source_id}/trigger", response_model=MessageResponse)
async def trigger_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    config = source.config or {}
    source_type = source.type.value

    try:
        if source_type == "hackernews":
            count = await search_hn(
                query=config.get("query", ""),
                source_id=source.id,
                tags=config.get("tags", "story"),
            )
            return {
                "message": f"Success! Fetched {count} items for '{source.name}'"
            }

        if source_type == "twitter":
            count = await search_x(
                query=config.get("query", ""),
                source_id=source.id,
                max_results=int(config.get("max_results", 50)),
                max_pages=int(config.get("max_pages", 1)),
            )
            return {
                "message": f"Success! Fetched {count} X posts for '{source.name}'"
            }

        return {
            "message": (
                f"Source '{source.name}' triggered, "
                "but no manual trigger handler exists for this type."
            )
        }
    except Exception as exc:
        source.error_message = str(exc)
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
