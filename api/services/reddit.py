import os
import praw
import asyncio
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import FeedItem, Source, SourceType
from database import AsyncSessionLocal

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "TrendRadar/1.0")

# Default subreddits rich in pain points and opportunities
DEFAULT_SUBREDDITS = [
    "problems",
    "startupideas",
    "entrepreneur",
    "SomebodyMakeThis",
    "needadvice",
    "AskReddit",
    "technology",
    "artificial",
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
    result = await db.execute(
        select(FeedItem).where(FeedItem.external_id == external_id)
    )
    return result.scalar_one_or_none() is not None


async def fetch_subreddit(
    subreddit_name: str,
    source_id: str,
    limit: int = 50,
    mode: str = "hot",          # hot | new | top | rising
) -> int:
    """
    Fetch posts from a subreddit and store new ones in the DB.
    Returns the count of new items inserted.
    """
    reddit = get_reddit_client()
    subreddit = reddit.subreddit(subreddit_name)

    fetch_fn = {
        "hot": subreddit.hot,
        "new": subreddit.new,
        "top": subreddit.top,
        "rising": subreddit.rising,
    }.get(mode, subreddit.hot)

    # PRAW is sync — run in thread pool to avoid blocking the event loop
    posts = await asyncio.get_event_loop().run_in_executor(
        None, lambda: list(fetch_fn(limit=limit))
    )

    inserted = 0
    async with AsyncSessionLocal() as db:
        for post in posts:
            if await _is_duplicate(post.id, db):
                continue
            item = FeedItem(**_build_feed_item(post, source_id))
            db.add(item)
            inserted += 1

        # Update source metadata
        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
        if source:
            source.last_fetched_at = datetime.utcnow()
            source.total_items_fetched += inserted
            source.error_message = None

        await db.commit()

    return inserted


async def fetch_all_configured_subreddits() -> dict:
    """
    Reads all active Reddit sources from DB and fetches each one.
    Called by the scheduler.
    """
    results = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(
                Source.type == SourceType.reddit,
                Source.is_active == True,
            )
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


async def stream_subreddit(subreddit_name: str, source_id: str, callback=None):
    """
    Real-time stream of new posts from a subreddit.
    Runs indefinitely — designed to be launched as a background task.
    Pass a callback(item) to handle each new FeedItem.
    """
    reddit = get_reddit_client()
    subreddit = reddit.subreddit(subreddit_name)

    def _stream():
        for post in subreddit.stream.submissions(skip_existing=True):
            yield _build_feed_item(post, source_id)

    loop = asyncio.get_event_loop()
    gen = await loop.run_in_executor(None, lambda: iter(_stream()))

    while True:
        item_data = await loop.run_in_executor(None, lambda: next(gen, None))
        if item_data is None:
            break

        async with AsyncSessionLocal() as db:
            if not await _is_duplicate(item_data["external_id"], db):
                item = FeedItem(**item_data)
                db.add(item)
                await db.commit()
                await db.refresh(item)
                if callback:
                    await callback(item)

        await asyncio.sleep(0.5)
