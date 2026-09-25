import httpx
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select

from models import FeedItem, Source, SourceType
from database import AsyncSessionLocal
from services.realtime import notify_new_items

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


async def _is_duplicate(external_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(FeedItem).where(FeedItem.external_id == str(external_id)))
        return result.scalar_one_or_none() is not None


def _build_feed_item(hit: dict, source_id: str) -> dict:
    return {
        "source_id": source_id,
        "external_id": str(hit.get("objectID", "")),
        "title": hit.get("title") or hit.get("story_title") or hit.get("comment_text", "")[:120],
        "body": hit.get("story_text") or hit.get("comment_text"),
        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        "author": hit.get("author"),
        "score": hit.get("points") or 0,
        "comment_count": hit.get("num_comments") or 0,
        "raw_data": {"tags": hit.get("_tags", []), "hn_id": hit.get("objectID")},
    }


async def search_hn(
    query: str,
    source_id: str,
    tags: str = "story",
    hours_back: int = 24,
    max_results: int = 50,
) -> int:
    since_ts = int((datetime.utcnow() - timedelta(hours=hours_back)).timestamp())
    params = {
        "query": query,
        "tags": tags,
        "numericFilters": f"created_at_i>{since_ts}",
        "hitsPerPage": max_results,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{HN_ALGOLIA_BASE}/search", params=params)
        response.raise_for_status()
        hits = response.json().get("hits", [])

    inserted = 0
    inserted_ids: list[str] = []

    async with AsyncSessionLocal() as db:
        for hit in hits:
            eid = str(hit.get("objectID", ""))
            if not eid or await _is_duplicate(eid):
                continue
            item = FeedItem(**_build_feed_item(hit, source_id))
            db.add(item)
            await db.flush()  # populate item.id before commit, for the notify payload
            inserted_ids.append(item.id)
            inserted += 1

        source = (await db.execute(select(Source).where(Source.id == source_id))).scalar_one_or_none()
        if source:
            source.last_fetched_at = datetime.utcnow()
            source.total_items_fetched += inserted
            source.error_message = None

        await db.commit()

    if inserted_ids:
        await notify_new_items(source_id, inserted_ids)

    return inserted


async def fetch_all_configured_hn_sources() -> dict:
    results = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(Source.type == SourceType.hackernews, Source.is_active == True)
        )
        sources = result.scalars().all()

    for source in sources:
        config = source.config or {}
        count = await search_hn(config.get("query", ""), source.id, tags=config.get("tags", "story"))
        results[source.name] = count
    return results