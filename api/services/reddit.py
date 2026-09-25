# api/services/reddit.py
import os
import praw
import asyncio
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import FeedItem, Source, SourceType
from database import AsyncSessionLocal
from services.realtime import notify_new_items

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "TrendRadar/1.0")

DEFAULT_SUBREDDITS = [
    "problems", "startupideas", "entrepreneur", "SomebodyMakeThis",
    "needadvice", "AskReddit", "technology", "artificial",
]


def get_reddit_client() -> praw.Reddit:
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
        read_only=True,
    )


def _build_feed_item(post, source_id: str) -> dict:
    return {
        "source_id": source_id,
        "external_id": post.id,
        "title": post.title,
        "body": post.selftext[:2000] if post.selftext else None,
        "url": f"https://reddit.com{post.permalink}",
        "author": str(post.author) if post.author else "[deleted]",
        "score": post.score,
        "comment_count": post.num_comments,
        "raw_data": {
            "subreddit": post.subreddit.display_name,
            "upvote_ratio": post.upvote_ratio,
            "flair": post.link_flair_text,
            "created_utc": post.created_utc,
        },
    }


async def _is_duplicate(external_id: str, db: AsyncSession) -> bool:
    result = await db.execute(select(FeedItem).where(FeedItem.external_id == external_id))
    return result.scalar_one_or_none() is not None


async def fetch_subreddit(
    subreddit_name: str,
    source_id: str,
    limit: int = 50,
    mode: str = "hot",
) -> int:
    reddit = get_reddit_client()
    subreddit = reddit.subreddit(subreddit_name)

    fetch_fn = {
        "hot": subreddit.hot, "new": subreddit.new,
        "top": subreddit.top, "rising": subreddit.rising,
    }.get(mode, subreddit.hot)

    posts = await asyncio.get_event_loop().run_in_executor(
        None, lambda: list(fetch_fn(limit=limit))
    )

    inserted = 0
    inserted_ids: list[str] = []
    async with AsyncSessionLocal() as db:
        for post in posts:
            if await _is_duplicate(post.id, db):
                continue
            item = FeedItem(**_build_feed_item(post, source_id))
            db.add(item)
            await db.flush()  # populate item.id before commit for the notify payload below
            inserted_ids.append(item.id)
            inserted += 1

        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
        if source:
            source.last_fetched_at = datetime.utcnow()
            source.total_items_fetched += inserted
            source.error_message = None

        await db.commit()

    if inserted_ids:
        await notify_new_items(source_id, inserted_ids)

    return inserted


async def fetch_all_configured_subreddits() -> dict:
    results = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(Source.type == SourceType.reddit, Source.is_active == True)
        )
        sources = result.scalars().all()

    for source in sources:
        subreddits = source.config.get("subreddits", DEFAULT_SUBREDDITS)
        mode = source.config.get("mode", "hot")
        limit = source.config.get("limit", 50)
        count = 0
        for sub in subreddits:
            try:
                count += await fetch_subreddit(sub, source.id, limit=limit, mode=mode)
            except Exception as e:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Source).where(Source.id == source.id))
                    s = result.scalar_one_or_none()
                    if s:
                        s.error_message = str(e)
                        await db.commit()
        results[source.name] = count

    return results