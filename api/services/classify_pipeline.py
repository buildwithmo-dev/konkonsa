# api/services/classify_pipeline.py
import asyncio
from sqlalchemy import select

from database import AsyncSessionLocal
from models import FeedItem, Classification, ItemType
from services.llm import call_llm   # was: `from groq_client import call_llm` — that module doesn't exist

CLASSIFICATION_PROMPT = """
You are a social listening analyst. Classify the following social media post and return ONLY valid JSON.

Post Title: {title}
Post Body: {body}

Return this exact JSON structure with no extra text:
{{
  "item_type": "pain_point | trend | opportunity | complaint | wish | unknown",
  "topic": "short topic label (max 5 words)",
  "summary": "1-2 sentence summary of the core issue or signal",
  "audience": "who is affected or interested (e.g. 'developers', 'small business owners')",
  "severity": 0.0 to 10.0,
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "sentiment": "positive | negative | neutral"
}}
"""

MAX_CONCURRENT = 5
BATCH_SIZE = 50


async def classify_single_item(item: FeedItem, db) -> Classification:
    """Classify one FeedItem via Groq and save the result."""
    title = (item.title or "")[:500]
    body = (item.body or "")[:1500]

    classification = Classification(feed_item_id=item.id)
    try:
        result = await call_llm(
            CLASSIFICATION_PROMPT.format(title=title, body=body),
            max_tokens=512,
            as_json=True,
            json_object=True,
        )
        raw_type = result.get("item_type", "unknown")
        try:
            classification.item_type = ItemType(raw_type)
        except ValueError:
            classification.item_type = ItemType.unknown
        classification.topic = result.get("topic")
        classification.summary = result.get("summary")
        classification.audience = result.get("audience")
        classification.severity = float(result.get("severity", 0.0))
        classification.keywords = result.get("keywords", [])
        classification.sentiment = result.get("sentiment")
    except Exception as e:
        classification.failed = True
        classification.error = str(e)

    db.add(classification)
    return classification


async def classify_pending_items(batch_size: int = BATCH_SIZE) -> dict:
    """Find all FeedItems without a Classification and classify them."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(FeedItem)
            .outerjoin(Classification)
            .where(Classification.id == None)
            .limit(batch_size)
        )
        items = result.scalars().all()

    if not items:
        return {"processed": 0, "failed": 0}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def safe_classify(item: FeedItem):
        async with semaphore:
            async with AsyncSessionLocal() as db:
                existing = await db.execute(
                    select(Classification).where(Classification.feed_item_id == item.id)
                )
                if existing.scalar_one_or_none():
                    return None
                cls = await classify_single_item(item, db)
                await db.commit()
                return cls

    results = await asyncio.gather(*[safe_classify(item) for item in items], return_exceptions=True)
    processed = sum(1 for r in results if r is not None and not isinstance(r, Exception))
    failed = sum(1 for r in results if isinstance(r, Exception))
    return {"processed": processed, "failed": failed}