"""Build dashboard insights from the live classified feed."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select

from database import AsyncSessionLocal
from models import Classification, FeedItem, ItemType, PainPoint, Trend


def _mode(values: list[str | None]) -> str | None:
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else None


async def refresh_insights() -> dict[str, int]:
    """Materialize Trends and PainPoints from current classifications.

    The raw feed remains the source of truth; these tables are a query-friendly
    projection used by the dashboard.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Classification, FeedItem)
            .join(FeedItem, FeedItem.id == Classification.feed_item_id)
            .where(Classification.failed.is_(False))
        )
        rows = result.all()

        if not rows:
            return {"trends": 0, "pain_points": 0}

        groups: dict[str, list[tuple[Classification, FeedItem]]] = {}
        for row in rows:
            classification, item = row
            topic = (classification.topic or "").strip()
            if not topic:
                continue
            groups.setdefault(topic, []).append((classification, item))

        now = datetime.utcnow()
        rising_cutoff = now - timedelta(hours=24)
        trend_count = 0
        pain_count = 0

        for topic, group in groups.items():
            classifications = [c for c, _ in group]
            items = [i for _, i in group]
            volume = len(group)
            severities = [float(c.severity or 0) for c in classifications]
            avg_severity = sum(severities) / len(severities) if severities else 0
            recent = sum(1 for i in items if i.fetched_at and i.fetched_at >= rising_cutoff)
            historical_days = max((now - min((i.fetched_at for i in items if i.fetched_at), default=now)).total_seconds() / 86400, 1)
            expected_daily = volume / historical_days
            is_rising = recent > expected_daily
            score = min(100.0, round((min(volume, 100) / 100) * 70 + avg_severity * 3, 1))
            keywords = [kw for c in classifications for kw in (c.keywords or [])]
            top_keywords = [kw for kw, _ in Counter(keywords).most_common(5)]
            audience = _mode([c.audience for c in classifications])
            category = _mode([c.item_type.value if hasattr(c.item_type, "value") else str(c.item_type) for c in classifications]) or ItemType.trend.value
            first_seen = min((i.fetched_at for i in items if i.fetched_at), default=now)
            last_seen = max((i.fetched_at for i in items if i.fetched_at), default=now)

            existing = (await db.execute(select(Trend).where(Trend.title == topic))).scalar_one_or_none()
            if existing:
                existing.description = next((c.summary for c in classifications if c.summary), existing.description)
                try:
                    existing.category = ItemType(category)
                except ValueError:
                    existing.category = ItemType.trend
                existing.volume = volume
                existing.score = score
                existing.keywords = top_keywords
                existing.audience = audience
                existing.is_rising = is_rising
                existing.first_seen_at = first_seen
                existing.last_seen_at = last_seen
            else:
                db.add(Trend(
                    title=topic,
                    description=next((c.summary for c in classifications if c.summary), f"Live signal around {topic}"),
                    category=ItemType(category) if category in {x.value for x in ItemType} else ItemType.trend,
                    volume=volume,
                    score=score,
                    keywords=top_keywords,
                    audience=audience,
                    is_rising=is_rising,
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                ))
            trend_count += 1

            pain_classifications = [c for c in classifications if c.item_type in {ItemType.pain_point, ItemType.complaint} or float(c.severity or 0) >= 6]
            if pain_classifications:
                pain_title = topic
                pain_items = [i for c, i in group if c in pain_classifications]
                pain_severity = sum(float(c.severity or 0) for c in pain_classifications) / len(pain_classifications)
                pain_keywords = [kw for c in pain_classifications for kw in (c.keywords or [])]
                pain_existing = (await db.execute(select(PainPoint).where(PainPoint.title == pain_title))).scalar_one_or_none()
                if pain_existing:
                    pain_existing.description = next((c.summary for c in pain_classifications if c.summary), pain_existing.description)
                    pain_existing.audience = _mode([c.audience for c in pain_classifications])
                    pain_existing.severity = round(pain_severity, 1)
                    pain_existing.frequency = len(pain_classifications)
                    pain_existing.keywords = [kw for kw, _ in Counter(pain_keywords).most_common(5)]
                else:
                    db.add(PainPoint(
                        title=pain_title,
                        description=next((c.summary for c in pain_classifications if c.summary), f"Recurring friction around {topic}"),
                        audience=_mode([c.audience for c in pain_classifications]),
                        severity=round(pain_severity, 1),
                        frequency=len(pain_classifications),
                        keywords=[kw for kw, _ in Counter(pain_keywords).most_common(5)],
                    ))
                pain_count += 1

        await db.commit()
        return {"trends": trend_count, "pain_points": pain_count}
