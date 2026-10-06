# api/services/reddit.py
import asyncio
import os

import praw
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Source, SourceType
from services.ingest import persist_items

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "Konkonsa/1.0")

DEFAULT_SUBREDDITS = [
    "problems", "startupideas", "entrepreneur", "SomebodyMakeThis",
    "needadvice", "AskReddit", "technology", "artificial",
]
# Use with config {"preset": "africa"}. Verify these exist/are active before relying on them.
AFRICA_SUBREDDITS = [
    "Africa", "Nigeria", "Kenya", "southafrica", "Ghana", "Egypt", "Uganda",
    "Ethiopia", "Tanzania", "Zimbabwe", "Rwanda", "Zambia", "Lagos", "Nairobi",
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
            "platform": "reddit",
            "subreddit": post.subreddit.display_name,
            "upvote_ratio": post.upvote_ratio,
            "flair": post.link_flair_text,
            "created_utc": post.created_utc,
        },
    }


async def fetch_subreddit(
    subreddit_name: str,
    source_id: str,
    limit: int = 50,
    mode: str = "hot",
    africa_only: bool = False,
) -> int:
    subreddit = get_reddit_client().subreddit(subreddit_name)
    fetch_fn = {
        "hot": subreddit.hot, "new": subreddit.new,
        "top": subreddit.top, "rising": subreddit.rising,
    }.get(mode, subreddit.hot)

    posts = await asyncio.to_thread(lambda: list(fetch_fn(limit=limit)))
    items = []
    for post in posts:
        item = _build_feed_item(post, source_id)
        item["_hint"] = subreddit_name  # r/ghana etc. is itself a strong geo signal
        items.append(item)
    return await persist_items(source_id, items, africa_only=africa_only)


async def fetch_all_configured_subreddits() -> dict:
    results: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        sources = (await db.execute(
            select(Source).where(Source.type == SourceType.reddit, Source.is_active.is_(True))
        )).scalars().all()

    for source in sources:
        cfg = source.config or {}
        default = AFRICA_SUBREDDITS if cfg.get("preset") == "africa" else DEFAULT_SUBREDDITS
        subreddits = cfg.get("subreddits") or default
        mode, limit = cfg.get("mode", "hot"), cfg.get("limit", 50)
        africa_only = bool(cfg.get("africa_only", cfg.get("preset") == "africa"))

        count, errors = 0, []
        for sub in subreddits:
            try:
                count += await fetch_subreddit(sub, source.id, limit=limit, mode=mode, africa_only=africa_only)
            except Exception as e:
                errors.append(f"r/{sub}: {e}")
            await asyncio.sleep(1)  # stay well inside Reddit's rate limit

        if errors:
            async with AsyncSessionLocal() as db:
                s = await db.get(Source, source.id)
                if s:
                    s.error_message = "; ".join(errors)[:500]
                    await db.commit()
        results[source.name] = count
    return results