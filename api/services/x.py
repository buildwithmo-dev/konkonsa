from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select

from database import AsyncSessionLocal
from models import FeedItem, Source, SourceType
from services.realtime import notify_new_items


X_API_URL = "https://api.x.com/2/tweets/search/recent"

DEFAULT_QUERY = os.getenv(
    "X_SEARCH_QUERY",
    '("AI" OR startup OR "small business" OR "side hustle") lang:en -is:retweet',
)
DEFAULT_MAX_RESULTS = int(os.getenv("X_FETCH_MAX_RESULTS", "50"))
DEFAULT_MAX_PAGES = int(os.getenv("X_FETCH_MAX_PAGES", "1"))

X_TWEET_FIELDS = ",".join(
    [
        "id",
        "text",
        "author_id",
        "created_at",
        "public_metrics",
        "lang",
        "conversation_id",
    ]
)

X_USER_FIELDS = ",".join(["id", "name", "username", "verified"])


def get_x_bearer_token() -> str:
    """Return the configured X bearer token without exposing it to callers."""
    token = (
        os.getenv("X_BEARER_TOKEN")
        or os.getenv("x_bearer_token")
        or os.getenv("BEARER_TOKEN")
        or ""
    ).strip()

    if not token:
        raise RuntimeError(
            "X_BEARER_TOKEN is not configured. "
            "Set X_BEARER_TOKEN (or x_bearer_token) in Render."
        )

    return token


def _build_feed_item(
    post: dict[str, Any],
    source_id: str,
    users_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    post_id = str(post["id"])
    text = post.get("text") or ""

    author_id = post.get("author_id")
    author = users_by_id.get(str(author_id), {}) if author_id else {}
    username = author.get("username")
    display_name = author.get("name")
    author_display = (
        f"@{username}" if username else display_name or None
    )

    metrics = post.get("public_metrics") or {}

    return {
        "source_id": source_id,
        "external_id": post_id,
        "title": text[:280],
        "body": text[:4000] if text else None,
        "url": f"https://x.com/i/web/status/{post_id}",
        "author": author_display,
        "score": int(metrics.get("like_count", 0)),
        "comment_count": int(metrics.get("reply_count", 0)),
        "raw_data": {
            "platform": "x",
            "post_id": post_id,
            "author_id": author_id,
            "author_username": username,
            "author_name": display_name,
            "created_at": post.get("created_at"),
            "conversation_id": post.get("conversation_id"),
            "lang": post.get("lang"),
            "public_metrics": metrics,
            "verified": author.get("verified"),
        },
    }


async def _existing_external_ids(
    db,
    source_id: str,
    external_ids: list[str],
) -> set[str]:
    if not external_ids:
        return set()

    result = await db.execute(
        select(FeedItem.external_id).where(
            FeedItem.source_id == source_id,
            FeedItem.external_id.in_(external_ids),
        )
    )
    return {str(row[0]) for row in result.all()}


async def search_x(
    query: str,
    source_id: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> int:
    """Fetch recent X posts and persist only new posts for the source."""
    token = get_x_bearer_token()

    query = (query or "").strip()
    if not query:
        raise ValueError(
            "X source requires a non-empty search query in source.config['query'] "
            "or X_SEARCH_QUERY."
        )

    max_results = max(10, min(int(max_results), 100))
    max_pages = max(1, min(int(max_pages), 10))

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    params: dict[str, Any] = {
        "query": query,
        "max_results": max_results,
        "tweet.fields": X_TWEET_FIELDS,
        "expansions": "author_id",
        "user.fields": X_USER_FIELDS,
    }

    inserted_total = 0
    next_token: str | None = None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers=headers,
    ) as client:
        for page_number in range(max_pages):
            request_params = dict(params)
            if next_token:
                request_params["next_token"] = next_token

            response = await client.get(X_API_URL, params=request_params)

            if response.status_code == 401:
                raise RuntimeError(
                    "X API rejected the bearer token (401). "
                    "Check that the Render X bearer token is valid."
                )
            if response.status_code == 403:
                raise RuntimeError(
                    "X API returned 403 Forbidden. "
                    "Check the X developer project/app access and API entitlement."
                )
            if response.status_code == 429:
                reset = response.headers.get("x-rate-limit-reset")
                suffix = f" Rate-limit reset: {reset}." if reset else ""
                raise RuntimeError(f"X API rate limit exceeded (429).{suffix}")
            if response.status_code >= 400:
                try:
                    error_body = response.json()
                except Exception:
                    error_body = response.text[:500]
                raise RuntimeError(
                    f"X API request failed ({response.status_code}): {error_body}"
                )

            payload = response.json()
            posts = payload.get("data") or []
            if not posts:
                break

            includes = payload.get("includes") or {}
            users_by_id = {
                str(user["id"]): user
                for user in includes.get("users") or []
                if user.get("id")
            }

            page_inserted = 0
            page_inserted_ids: list[str] = []

            async with AsyncSessionLocal() as db:
                external_ids = [
                    str(post["id"])
                    for post in posts
                    if post.get("id")
                ]
                existing_ids = await _existing_external_ids(
                    db, source_id, external_ids
                )

                for post in posts:
                    post_id = str(post.get("id", ""))
                    if not post_id or post_id in existing_ids:
                        continue

                    item = FeedItem(**_build_feed_item(post, source_id, users_by_id))
                    db.add(item)
                    await db.flush()  # populate item.id before commit
                    page_inserted_ids.append(item.id)
                    page_inserted += 1

                source_result = await db.execute(
                    select(Source).where(Source.id == source_id)
                )
                source = source_result.scalar_one_or_none()
                if source:
                    source.last_fetched_at = datetime.utcnow()
                    source.total_items_fetched = (
                        (source.total_items_fetched or 0) + page_inserted
                    )
                    source.error_message = None

                await db.commit()

            if page_inserted_ids:
                await notify_new_items(source_id, page_inserted_ids)

            inserted_total += page_inserted
            meta = payload.get("meta") or {}
            next_token = meta.get("next_token")
            if not next_token or page_number + 1 >= max_pages:
                break

    return inserted_total


async def fetch_all_configured_x_sources() -> dict[str, int]:
    """Fetch every active X source configured in the database."""
    results: dict[str, int] = {}

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(
                Source.type == SourceType.twitter,
                Source.is_active.is_(True),
            )
        )
        sources = result.scalars().all()

    for source in sources:
        config = source.config or {}
        query = config.get("query") or DEFAULT_QUERY
        max_results = int(config.get("max_results", DEFAULT_MAX_RESULTS))
        max_pages = int(config.get("max_pages", DEFAULT_MAX_PAGES))

        try:
            results[source.name] = await search_x(
                query=query,
                source_id=source.id,
                max_results=max_results,
                max_pages=max_pages,
            )
        except Exception as exc:
            results[source.name] = 0
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Source).where(Source.id == source.id)
                )
                db_source = result.scalar_one_or_none()
                if db_source:
                    db_source.error_message = str(exc)
                    await db.commit()

    return results