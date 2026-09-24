import asyncio
import re
from datetime import datetime

import feedparser
import httpx
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

GEO = "GH"   # Default: Ghana. Use "US", "GB", "NG", "KE", etc. (RSS needs a country code)

TRENDS_RSS = "https://trends.google.com/trending/rss"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Konkonsa/1.0)"}


def _get_pytrends() -> TrendReq:
    return TrendReq(hl="en-US", tz=0, timeout=(10, 25))


def _parse_traffic(raw: str) -> int:
    """'200+' -> 200, '2K+' -> 2000, '1M+' -> 1000000."""
    m = re.match(r"\s*([\d,.]+)\s*([KM]?)", raw or "", re.I)
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    mult = {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2).upper()]
    return int(num * mult)


async def fetch_interest_over_time(keywords: list[str], timeframe: str = "now 7-d") -> dict:
    """Returns {keyword: [{date, value}]} for up to 5 keywords."""
    pytrends = _get_pytrends()

    def _fetch():
        pytrends.build_payload(keywords[:5], timeframe=timeframe, geo=GEO)
        return pytrends.interest_over_time()

    df = await asyncio.get_running_loop().run_in_executor(None, _fetch)

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
    """Top and rising related queries for a keyword."""
    pytrends = _get_pytrends()

    def _fetch():
        pytrends.build_payload([keyword], timeframe="now 7-d", geo=GEO)
        return pytrends.related_queries()

    data = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    result = data.get(keyword, {})

    output = {}
    for key in ("top", "rising"):
        df = result.get(key)
        if df is not None and not df.empty:
            output[key] = df.to_dict(orient="records")

    return output


async def fetch_trending_searches(geo: str = GEO) -> list[dict]:
    """Today's trending searches from Google's public RSS feed (replaces pytrends.trending_searches)."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=HEADERS) as client:
        resp = await client.get(TRENDS_RSS, params={"geo": geo or "US"})
        resp.raise_for_status()

    feed = feedparser.parse(resp.text)
    return [
        {
            "term": e.title,
            "traffic": _parse_traffic(e.get("ht_approx_traffic", "")),
        }
        for e in feed.entries[:20]
    ]


async def sync_trends_to_db(source_id: str) -> int:
    """
    Stores trending searches as FeedItems and keyword interest as Trend records.
    Each step fails independently. Returns number of new FeedItems created.
    """
    inserted = 0
    errors: list[str] = []

    # Step 1: today's trending searches (non-fatal)
    try:
        trending = await fetch_trending_searches()
    except Exception as e:
        trending = []
        errors.append(f"trending: {e}")
        print(f"[google_trends] trending searches failed: {e}")

    async with AsyncSessionLocal() as db:
        for t in trending:
            term = t["term"]
            external_id = f"gt_trending_{term.replace(' ', '_')}_{datetime.utcnow().date()}"

            dup = await db.execute(select(FeedItem).where(FeedItem.external_id == external_id))
            if dup.scalar_one_or_none():
                continue

            db.add(FeedItem(
                source_id=source_id,
                external_id=external_id,
                title=f"Trending: {term}",
                body=None,
                url=f"https://trends.google.com/trends/explore?q={term.replace(' ', '+')}&geo={GEO}",
                score=t["traffic"],
                raw_data={"type": "trending_search", "term": term, "approx_traffic": t["traffic"]},
            ))
            inserted += 1

        await db.commit()

    # Step 2: interest over time for configured keywords
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
        keywords = source.config.get("keywords", DEFAULT_KEYWORDS) if source else DEFAULT_KEYWORDS

    for i in range(0, len(keywords), 5):  # pytrends limit: 5 per request
        batch = keywords[i:i + 5]
        try:
            interest = await fetch_interest_over_time(batch, timeframe="now 7-d")

            async with AsyncSessionLocal() as db:
                for kw, data_points in interest.items():
                    if not data_points:
                        continue

                    values = [d["value"] for d in data_points]
                    avg = sum(values) / len(values)
                    latest = values[-1]
                    is_rising = latest > avg * 1.3

                    existing = await db.execute(select(Trend).where(Trend.title == kw))
                    trend = existing.scalar_one_or_none()
                    if trend:
                        trend.is_rising = is_rising
                        trend.score = float(latest)
                        trend.last_seen_at = datetime.utcnow()
                        trend.volume += 1
                    else:
                        db.add(Trend(
                            title=kw,
                            description=f"Google Trends keyword: {kw}",
                            category=ItemType.trend,
                            volume=1,
                            score=float(latest),
                            keywords=[kw],
                            is_rising=is_rising,
                        ))

                await db.commit()

        except Exception as e:
            errors.append(f"keywords {batch}: {e}")
            print(f"[google_trends] Error for batch {batch}: {e}")

        await asyncio.sleep(2)  # avoid rate limiting

    # Update source; surface partial failures in the UI
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
        if source:
            source.last_fetched_at = datetime.utcnow()
            source.total_items_fetched += inserted
            source.error_message = "; ".join(errors)[:500] if errors else None
            await db.commit()

    return inserted


async def fetch_all_configured_trends_sources() -> dict:
    """Called by the scheduler to run all active Google Trends sources."""
    results = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Source).where(
                Source.type == SourceType.google_trends,
                Source.is_active == True,  # noqa: E712
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
                    s.error_message = str(e)[:500]
                    await db.commit()
            results[source.name] = 0

    return results