# api/services/aggregation.py
"""
Rolls raw Classification rows up into aggregated PainPoint / Trend records.

This is the missing link between "an LLM classified one social post" and
"the dashboard has a ranked list of pain points and trends to act on."
Without this, /painpoints and most of /trends stay empty forever regardless
of how much ingestion and classification happens upstream.

Matching strategy: embed each new signal's (topic + summary + title), compare
by cosine similarity against a recent pool of existing PainPoint/Trend
embeddings, and either merge into the closest match above SIMILARITY_THRESHOLD
or create a new record. This reuses the same embedding model already used for
clustering (services/embeddings.py) rather than introducing a second one.

Runs on a schedule (see services/scheduler.py: job_synthesize_insights) and can
be triggered on demand via POST /jobs/{job_id}/run for the "synthesize_insights"
job once seeded.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from database import AsyncSessionLocal
from models import Classification, FeedItem, PainPoint, Trend, ItemType
from services.embeddings import embed_texts, cosine_similarity

log = logging.getLogger("aggregation")

BATCH_SIZE = 200
SIMILARITY_THRESHOLD = 0.72   # cosine similarity to treat two signals as "the same" underlying issue
CANDIDATE_POOL = 300          # how many recent existing records to compare each new signal against

# Which ItemType values roll into which aggregate table. Tunable.
PAIN_POINT_TYPES = {ItemType.pain_point, ItemType.complaint}
TREND_TYPES = {ItemType.trend, ItemType.opportunity, ItemType.wish}


def _text_for(topic: Optional[str], summary: Optional[str], title: Optional[str] = None) -> str:
    return f"{topic or ''} {summary or ''} {title or ''}".strip()


async def synthesize_insights(batch_size: int = BATCH_SIZE) -> dict:
    """
    Find Classifications that haven't been rolled up yet, and either merge them
    into an existing PainPoint/Trend (by semantic similarity) or create a new one.

    Returns {"processed", "pain_points_created", "pain_points_updated",
             "trends_created", "trends_updated", "skipped"}
    """
    stats = {
        "processed": 0,
        "pain_points_created": 0, "pain_points_updated": 0,
        "trends_created": 0, "trends_updated": 0,
        "skipped": 0,
    }

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Classification, FeedItem)
            .join(FeedItem, FeedItem.id == Classification.feed_item_id)
            .where(Classification.failed == False)          # noqa: E712
            .where(Classification.promoted_at.is_(None))
            .order_by(Classification.classified_at.asc())
            .limit(batch_size)
        )
        rows = result.all()

    if not rows:
        return stats

    pain_point_rows = [r for r in rows if r.Classification.item_type in PAIN_POINT_TYPES]
    trend_rows = [r for r in rows if r.Classification.item_type in TREND_TYPES]
    unhandled_types = PAIN_POINT_TYPES | TREND_TYPES
    skipped = [r for r in rows if r.Classification.item_type not in unhandled_types]

    if pain_point_rows:
        created, updated = await _merge_into(pain_point_rows, PainPoint, "pain_point")
        stats["pain_points_created"] += created
        stats["pain_points_updated"] += updated

    if trend_rows:
        created, updated = await _merge_into(trend_rows, Trend, "trend")
        stats["trends_created"] += created
        stats["trends_updated"] += updated

    # Mark the whole batch (including unhandled types like "unknown") as
    # processed so it isn't re-read on the next run.
    async with AsyncSessionLocal() as db:
        all_ids = [r.Classification.id for r in rows]
        existing = await db.execute(select(Classification).where(Classification.id.in_(all_ids)))
        now = datetime.utcnow()
        for cls in existing.scalars().all():
            if cls.promoted_at is None:
                cls.promoted_at = now
        await db.commit()

    stats["processed"] = len(rows)
    stats["skipped"] = len(skipped)
    log.info("synthesize_insights: %s", stats)
    return stats


async def _merge_into(rows, model, kind: str) -> tuple[int, int]:
    """
    rows: list of (Classification, FeedItem) SQLAlchemy Row objects, all destined
    for `model` (PainPoint or Trend). Returns (created_count, updated_count).
    """
    created = 0
    updated = 0

    texts = [_text_for(r.Classification.topic, r.Classification.summary, r.FeedItem.title) for r in rows]
    new_vectors = await embed_texts(texts)

    order_column = model.updated_at if hasattr(model, "updated_at") else model.last_seen_at

    async with AsyncSessionLocal() as db:
        pool_result = await db.execute(
            select(model)
            .where(model.embedding.is_not(None))
            .order_by(order_column.desc())
            .limit(CANDIDATE_POOL)
        )
        pool = list(pool_result.scalars().all())

        for row, vec in zip(rows, new_vectors):
            cls, item = row.Classification, row.FeedItem

            best_match = None
            best_score = 0.0
            for existing in pool:
                if not existing.embedding:
                    continue
                score = cosine_similarity(vec, existing.embedding)
                if score > best_score:
                    best_score = score
                    best_match = existing

            if best_match is not None and best_score >= SIMILARITY_THRESHOLD:
                _merge_signal_into_record(best_match, cls, kind)
                db.add(best_match)
                if kind == "pain_point":
                    cls.pain_point_id = best_match.id
                else:
                    cls.trend_id = best_match.id
                updated += 1
            else:
                new_record = _new_record_from(cls, item, vec, kind)
                db.add(new_record)
                await db.flush()  # populate generated id before linking back
                if kind == "pain_point":
                    cls.pain_point_id = new_record.id
                else:
                    cls.trend_id = new_record.id
                pool.append(new_record)  # later rows in this same batch can match it too
                created += 1

            db.add(cls)

        await db.commit()

    return created, updated


def _merge_signal_into_record(record, cls: Classification, kind: str) -> None:
    """Fold one new classified signal into an existing PainPoint/Trend. Tunable heuristics."""
    now = datetime.utcnow()
    record.keywords = list(dict.fromkeys((record.keywords or []) + (cls.keywords or [])))[:12]
    if not record.audience and cls.audience:
        record.audience = cls.audience

    if kind == "pain_point":
        record.frequency = (record.frequency or 0) + 1
        # bias toward the higher of the two severities rather than a flat average,
        # so one severe report isn't diluted away by many mild ones.
        record.severity = round(
            max(record.severity or 0.0, (record.severity or 0.0) * 0.7 + (cls.severity or 0.0) * 0.3), 2
        )
        record.updated_at = now
    else:
        record.volume = (record.volume or 0) + 1
        record.score = round((record.score or 0.0) * 0.7 + (cls.severity or 0.0) * 10 * 0.3, 2)
        record.last_seen_at = now
        record.is_rising = record.volume >= 5 and (record.score or 0.0) > 40.0


def _new_record_from(cls: Classification, item: FeedItem, vec: list[float], kind: str):
    keywords = (cls.keywords or [])[:12]
    title = (cls.topic or item.title or "Untitled")[:200]

    if kind == "pain_point":
        return PainPoint(
            title=title,
            description=cls.summary,
            audience=cls.audience,
            severity=cls.severity or 0.0,
            frequency=1,
            keywords=keywords,
            embedding=vec,
        )
    return Trend(
        title=title,
        description=cls.summary,
        category=cls.item_type,
        volume=1,
        score=round((cls.severity or 0.0) * 10, 2),
        keywords=keywords,
        audience=cls.audience,
        is_rising=False,
        embedding=vec,
    )