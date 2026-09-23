import asyncio
from datetime import datetime
from pytrends.request import TrendReq
from sqlalchemy import select

from models import FeedItem, Source, SourceType, Trend, ItemType
from database import AsyncSessionLocal

DEFAULT_KEYWORDS = [
    "AI tools",
    "side hustle",
    "remote work tools",
    "productivity app",
    "no code",
    "automation",
    "subscription fatigue",
    "mental health app",
]

GEO = "GH"   # Default: Ghana — change to "" for worldwide or "US", "GB", etc.


def _get_pytrends() -> TrendReq:
    return TrendReq(hl="en-US", tz=0, timeout=(10, 25))


async def fetch_interest_over_time(keywords: list[str], timeframe: str = "now 7-d") -> dict:
    """
    Fetch Google Trends interest-over-time for a list of keywords.
    Returns a dict of {keyword: [{date, value}]}
    """
    pytrends = _get_pytrends()

    # pytrends is sync — run in thread pool
    def _fetch():
        pytrends.build_payload(keywords[:5], timeframe=timeframe, geo=GEO)
        return pytrends.interest_over_time()

    df = await asyncio.get_event_loop().run_in_executor(None, _fetch)

    if df is None or df.empty:
        return {}

    result = {}
    for kw in keywords[:5]:
        if kw in df.columns:
            result[kw] = [
                {"date": str(idx.date()), "value": int(row[kw])}
                for idx, row in df.iterrows()
            ]
    return result


async def fetch_related_queries(keyword: str) -> dict:
    """
    Get top and rising related queries for a keyword.
    Useful for discovering emerging pain points.
    """
    pytrends = _get_pytrends()

    def _fetch():
        pytrends.build_payload([keyword], timeframe="now 7-d", geo=GEO)
        return pytrends.related_queries()

    data = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    result = data.get(keyword, {})

    output = {}
    for key in ("top", "rising"):
        df = result.get(key)
        if df is not None and not df.empty:
            output[key] = df.to_dict(orient="records")

    return output


async def fetch_trending_searches(geo: str = GEO) -> list[str]:
    """Get today's trending searches for a country."""
    pytrends = _get_pytrends()

    def _fetch():
        df = pytrends.trending_searches(pn=geo.lower() if geo else "united_states")
        return df[0].tolist() if not df.empty else []

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def fetch_realtime_trending(geo: str = GEO) -> list[dict]:
    """Get real-time trending searches (last 24h)."""
    pytrends = _get_pytrends()

    def _fetch():
        df = pytrends.realtime_trending_searches(pn=geo if geo else "US")
        if df is None or df.empty:
            return []
        return df.head(20).to_dict(orient="records")

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def sync_trends_to_db(source_id: str) -> int:
    """
    Fetches trending searches + related queries and stores them
    as FeedItems and Trend records.
    Returns number of new items created.
    """
    inserted = 0

    # Step 1: Get today's trending searches
    trending = await fetch_trending_searches()

    async with AsyncSessionLocal() as db:
        for term in trending[:20]:
            # Store as FeedItem
            from models import FeedItem
            item = FeedItem(
                source_id=source_id,
                external_id=f"gt_trending_{term.replace(' ', '_')}_{datetime.utcnow().date()}",
                title=f"Trending: {term}",
                body=None,
                url=f"https://trends.google.com/trends/explore?q={term.replace(' ', '+')}",
                score=0,
                raw_data={"type": "trending_search", "term": term},
            )
            # Check for duplicate
            dup = await db.execute(
                select(FeedItem).where(FeedItem.external_id == item.external_id)
            )
            if dup.scalar_one_or_none():
                continue

            db.add(item)
            inserted += 1

        await db.commit()

    # Step 2: Fetch interest over time for configured keywords
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
        keywords = source.config.get("keywords", DEFAULT_KEYWORDS) if source else DEFAULT_KEYWORDS

    # Batch keywords in groups of 5 (pytrends limit)
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i+5]
        try:
            interest = await fetch_interest_over_time(batch, timeframe="now 7-d")

            async with AsyncSessionLocal() as db:
                for kw, data_points in interest.items():
                    if not data_points:
                        continue

                    # Determine if rising: last value > average
                    values = [d["value"] for d in data_points]
                    avg = sum(values) / len(values) if values else 0
                    latest = values[-1] if values else 0
                    is_rising = latest > avg * 1.3

                    # Upsert Trend record
                    existing = await db.execute(
                        select(Trend).where(Trend.title == kw)
                    )
                    trend = existing.scalar_one_or_none()
                    if trend:
                        trend.is_rising = is_rising
                        trend.last_seen_at = datetime.utcnow()
                        trend.volume += 1
                    else:
                        trend = Trend(
                            title=kw,
                            description=f"Google Trends keyword: {kw}",
                            category=ItemType.trend,
                            volume=1,
                            score=float(latest),
                            keywords=[kw],
                            is_rising=is_rising,
                        )
                        db.add(trend)

                await db.commit()

        except Exception as e:
            print(f"[google_trends] Error for batch {batch}: {e}")

        await asyncio.sleep(1)  # avoid rate limiting

    # Update source
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
        if source:
            source.last_fetched_at = datetime.utcnow()
            source.total_items_fetched += inserted
            source.error_message = None
            await db.commit()

    return inserted


async def fetch_all_configured_trends_sources() -> dict:
    """Called by the scheduler to run all active Google Trends sources."""
    results = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(
                Source.type == SourceType.google_trends,
                Source.is_active == True,
            )
        )
        sources = result.scalars().all()

    for source in sources:
        try:
            count = await sync_trends_to_db(source.id)
            results[source.name] = count
        except Exception as e:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Source).where(Source.id == source.id))
                s = result.scalar_one_or_none()
                if s:
                    s.error_message = str(e)
                    await db.commit()
            results[source.name] = 0

    return results
