import asyncio
import os
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, AsyncSessionLocal
from models import FeedItem, Classification, ItemType
from schemas import ClassifyRequest, ClassifyBatchRequest, ClassificationOut, MessageResponse
from services.llm import call_llm
from services.rate_limit import rate_limiter

router = APIRouter(prefix="/classify", tags=["Classification"])

# Overridable via env — see env.example. Defaults are generous for a solo/small
# team dashboard but stop an open endpoint from burning through Groq quota.
CLASSIFY_RATE_LIMIT = int(os.getenv("CLASSIFY_RATE_LIMIT", "30"))
CLASSIFY_RATE_WINDOW_SECONDS = int(os.getenv("CLASSIFY_RATE_WINDOW_SECONDS", "3600"))

_classify_limit = Depends(
    rate_limiter("classify", limit=CLASSIFY_RATE_LIMIT, window_seconds=CLASSIFY_RATE_WINDOW_SECONDS)
)

CLASSIFICATION_PROMPT = """
You are a social listening analyst. Classify the following social media post and return ONLY valid JSON.

Post:
{text}

Return this exact JSON structure:
{{
  "item_type": "pain_point | trend | opportunity | complaint | wish | unknown",
  "topic": "short topic label",
  "summary": "1-2 sentence summary",
  "audience": "who is affected or interested",
  "severity": 0.0 to 10.0,
  "keywords": ["kw1", "kw2", "kw3"],
  "sentiment": "positive | negative | neutral"
}}
"""


# -----------------------------
# CLASSIFICATION LOGIC
# -----------------------------
async def classify_item(item: FeedItem, db: AsyncSession) -> Classification:
    """Classify a FeedItem via the LLM and persist the result."""
    text = f"{item.title or ''}\n{item.body or ''}".strip()

    stmt = select(Classification).where(Classification.feed_item_id == item.id)
    existing = await db.execute(stmt)
    classification = existing.scalar_one_or_none()

    if not classification:
        classification = Classification(feed_item_id=item.id)
        db.add(classification)

    try:
        result = await call_llm(
            prompt=CLASSIFICATION_PROMPT.format(text=text),
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
        classification.failed = False
        classification.error = None

    except Exception as e:
        classification.failed = True
        classification.error = str(e)

    await db.commit()
    await db.refresh(classification)
    return classification


# -----------------------------
# BACKGROUND BATCH PROCESSOR
# -----------------------------
async def process_batch_background(feed_item_ids: list[str]):
    """
    Background worker function that uses a dedicated AsyncSessionLocal.
    This prevents 'session closed' errors after the HTTP request finishes.
    """
    async with AsyncSessionLocal() as db:
        for item_id in feed_item_ids:
            result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
            item = result.scalar_one_or_none()

            if item:
                await classify_item(item, db)
                # Polite delay to respect Groq rate limits
                await asyncio.sleep(1.0)


# -----------------------------
# ROUTES
# -----------------------------
@router.post("", response_model=ClassificationOut, dependencies=[_classify_limit])
async def classify_single(payload: ClassifyRequest, db: AsyncSession = Depends(get_db)):
    """Creates a temporary FeedItem and runs immediate classification via the LLM."""
    dummy = FeedItem(title=payload.text, body=payload.context, source_id="manual")
    db.add(dummy)
    await db.commit()
    await db.refresh(dummy)

    return await classify_item(dummy, db)


@router.post("/batch", response_model=MessageResponse, dependencies=[_classify_limit])
async def classify_batch(
    payload: ClassifyBatchRequest,
    background_tasks: BackgroundTasks,
):
    """
    Triggers background processing for a batch of feed item IDs.

    Note: the rate limit here counts this ONE request, not each item inside
    the batch — a single call with 200 feed_item_ids still only costs 1 of
    the caller's allotted requests, but still burns 200 Groq calls in the
    background. If batch abuse becomes a real concern, cap len(feed_item_ids)
    here rather than relying on the request-level limiter to catch it.
    """
    background_tasks.add_task(process_batch_background, payload.feed_item_ids)
    return {"message": "Batch processing started in background."}


@router.post("/retry/{item_id}", response_model=ClassificationOut, dependencies=[_classify_limit])
async def retry_classification(item_id: str, db: AsyncSession = Depends(get_db)):
    """Retries classification for a failed or existing feed item."""
    result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Feed item not found")

    return await classify_item(item, db)


@router.get("/queue", response_model=list[str])
async def classification_queue(db: AsyncSession = Depends(get_db)):
    """Retrieves list of FeedItem IDs that have no associated classification. Read-only — no rate limit."""
    result = await db.execute(
        select(FeedItem.id).outerjoin(Classification).where(Classification.id == None)
    )
    return result.scalars().all()


@router.get("/failed", response_model=list[ClassificationOut])
async def failed_classifications(db: AsyncSession = Depends(get_db)):
    """Retrieves all classification records that marked failed=True. Read-only — no rate limit."""
    result = await db.execute(
        select(Classification).where(Classification.failed == True)
    )
    return result.scalars().all()