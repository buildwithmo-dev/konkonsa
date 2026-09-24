# from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import select
# import httpx, os, json, asyncio, random

# from database import get_db
# from models import FeedItem, Classification, ItemType
# from schemas import ClassifyRequest, ClassifyBatchRequest, ClassificationOut, MessageResponse

# from dotenv import load_dotenv
# load_dotenv()

# router = APIRouter(prefix="/classify", tags=["Classification"])

# GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# # ✅ Use fallback models (more reliable)
# MODELS = [
#     "gemini-2.0-flash",          # primary (stable)
#     "gemini-2.0-flash-lite",     # fallback (lighter)
# ]

# CLASSIFICATION_PROMPT = """
# You are a social listening analyst. Classify the following social media post and return ONLY valid JSON.

# Post:
# {text}

# Return this exact JSON structure:
# {{
#   "item_type": "pain_point | trend | opportunity | complaint | wish | unknown",
#   "topic": "short topic label",
#   "summary": "1-2 sentence summary",
#   "audience": "who is affected or interested",;
#   "severity": 0.0 to 10.0,
#   "keywords": ["kw1", "kw2", "kw3"],
#   "sentiment": "positive | negative | neutral"
# }}
# """

# # -----------------------------
# # 🔥 CORE GEMINI CALL
# # -----------------------------
# async def call_model(model: str, text: str) -> dict:
#     url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GOOGLE_API_KEY}"

#     payload = {
#         "contents": [
#             {"parts": [{"text": CLASSIFICATION_PROMPT.format(text=text)}]}
#         ],
#         "generationConfig": {
#             "response_mime_type": "application/json"
#         }
#     }

#     # Retry with exponential backoff
#     for attempt in range(4):
#         try:
#             async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
#                 response = await client.post(url, json=payload)

#             if response.status_code == 200:
#                 data = response.json()

#                 raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

#                 # ✅ Safe JSON parsing
#                 try:
#                     return json.loads(raw_text)
#                 except json.JSONDecodeError:
#                     raise Exception(f"Invalid JSON returned: {raw_text}")

#             if response.status_code in [429, 503]:
#                 wait_time = (2 ** attempt) + random.uniform(0, 2)
#                 print(f"{model} busy. Retry {attempt+1} in {wait_time:.2f}s...")
#                 await asyncio.sleep(wait_time)
#                 continue

#             raise Exception(f"{model} API Error {response.status_code}: {response.text}")

#         except httpx.RequestError as e:
#             wait_time = (2 ** attempt) + random.uniform(0, 2)
#             print(f"Network error: {e}. Retry in {wait_time:.2f}s...")
#             await asyncio.sleep(wait_time)

#     raise Exception(f"{model} failed after retries")


# # -----------------------------
# # 🔥 FALLBACK HANDLER
# # -----------------------------
# async def call_gemini(text: str) -> dict:
#     last_error = None

#     for model in MODELS:
#         try:
#             print(f"Trying model: {model}")
#             return await call_model(model, text)
#         except Exception as e:
#             print(f"{model} failed: {e}")
#             last_error = e

#     raise Exception(f"All models failed. Last error: {last_error}")


# # -----------------------------
# # 🔥 CLASSIFICATION LOGIC
# # -----------------------------
# async def classify_item(item: FeedItem, db: AsyncSession):
#     text = f"{item.title or ''}\n{item.body or ''}".strip()

#     stmt = select(Classification).where(Classification.feed_item_id == item.id)
#     existing = await db.execute(stmt)
#     classification = existing.scalar_one_or_none()

#     if not classification:
#         classification = Classification(feed_item_id=item.id)
#         db.add(classification)

#     try:
#         result = await call_gemini(text)

#         classification.item_type = ItemType(result.get("item_type", "unknown"))
#         classification.topic = result.get("topic")
#         classification.summary = result.get("summary")
#         classification.audience = result.get("audience")
#         classification.severity = float(result.get("severity", 0))
#         classification.keywords = result.get("keywords", [])
#         classification.sentiment = result.get("sentiment")
#         classification.failed = False
#         classification.error = None

#     except Exception as e:
#         classification.failed = True
#         classification.error = str(e)

#     await db.commit()
#     await db.refresh(classification)
#     return classification


# # -----------------------------
# # 🔥 ROUTES
# # -----------------------------
# @router.post("", response_model=ClassificationOut)
# async def classify_single(payload: ClassifyRequest, db: AsyncSession = Depends(get_db)):
#     dummy = FeedItem(title=payload.text, body=payload.context, source_id="manual")
#     db.add(dummy)
#     await db.commit()
#     await db.refresh(dummy)

#     return await classify_item(dummy, db)


# @router.post("/batch", response_model=MessageResponse)
# async def classify_batch(
#     payload: ClassifyBatchRequest,
#     background_tasks: BackgroundTasks,
#     db: AsyncSession = Depends(get_db),
# ):
#     async def run_batch():
#         for item_id in payload.feed_item_ids:
#             result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
#             item = result.scalar_one_or_none()

#             if item:
#                 await classify_item(item, db)

#                 # ✅ Free-tier safe delay
#                 await asyncio.sleep(5)

#     background_tasks.add_task(run_batch)
#     return {"message": "Batch started. Processing with 5s delay between items."}


# @router.post("/retry/{item_id}", response_model=ClassificationOut)
# async def retry_classification(item_id: str, db: AsyncSession = Depends(get_db)):
#     result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
#     item = result.scalar_one_or_none()

#     if not item:
#         raise HTTPException(status_code=404, detail="Feed item not found")

#     return await classify_item(item, db)


# @router.get("/queue", response_model=list[str])
# async def classification_queue(db: AsyncSession = Depends(get_db)):
#     result = await db.execute(
#         select(FeedItem.id).outerjoin(Classification).where(Classification.id == None)
#     )
#     return result.scalars().all()


# @router.get("/failed", response_model=list[ClassificationOut])
# async def failed_classifications(db: AsyncSession = Depends(get_db)):
#     result = await db.execute(
#         select(Classification).where(Classification.failed == True)
#     )
#     return result.scalars().all()

import asyncio
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, AsyncSessionLocal
from models import FeedItem, Classification, ItemType
from schemas import ClassifyRequest, ClassifyBatchRequest, ClassificationOut, MessageResponse

# Import your Groq helper function (adjust path as needed)
from services.llm import call_llm

router = APIRouter(prefix="/classify", tags=["Classification"])

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
# 🔥 CLASSIFICATION LOGIC
# -----------------------------
async def classify_item(item: FeedItem, db: AsyncSession) -> Classification:
    """Classify a FeedItem using Groq and persist the result."""
    text = f"{item.title or ''}\n{item.body or ''}".strip()

    stmt = select(Classification).where(Classification.feed_item_id == item.id)
    existing = await db.execute(stmt)
    classification = existing.scalar_one_or_none()

    if not classification:
        classification = Classification(feed_item_id=item.id)
        db.add(classification)

    try:
        # Call Groq LLM with native JSON mode enabled
        result = await call_llm(
            prompt=CLASSIFICATION_PROMPT.format(text=text),
            max_tokens=512,
            as_json=True,
            json_object=True,
        )

        # Parse output into enum safely
        raw_type = result.get("item_type", "unknown")
        try:
            classification.item_type = ItemType(raw_type)
        except ValueError:
            classification.item_type = ItemType.UNKNOWN

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
# 🔥 BACKGROUND BATCH PROCESSOR
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
# 🔥 ROUTES
# -----------------------------
@router.post("", response_model=ClassificationOut)
async def classify_single(payload: ClassifyRequest, db: AsyncSession = Depends(get_db)):
    """Creates a temporary FeedItem and runs immediate classification via Groq."""
    dummy = FeedItem(title=payload.text, body=payload.context, source_id="manual")
    db.add(dummy)
    await db.commit()
    await db.refresh(dummy)

    return await classify_item(dummy, db)


@router.post("/batch", response_model=MessageResponse)
async def classify_batch(
    payload: ClassifyBatchRequest,
    background_tasks: BackgroundTasks,
):
    """Triggers background processing for a batch of feed item IDs."""
    background_tasks.add_task(process_batch_background, payload.feed_item_ids)
    return {"message": "Batch processing started in background."}


@router.post("/retry/{item_id}", response_model=ClassificationOut)
async def retry_classification(item_id: str, db: AsyncSession = Depends(get_db)):
    """Retries classification for a failed or existing feed item."""
    result = await db.execute(select(FeedItem).where(FeedItem.id == item_id))
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Feed item not found")

    return await classify_item(item, db)


@router.get("/queue", response_model=list[str])
async def classification_queue(db: AsyncSession = Depends(get_db)):
    """Retrieves list of FeedItem IDs that have no associated classification."""
    result = await db.execute(
        select(FeedItem.id).outerjoin(Classification).where(Classification.id == None)
    )
    return result.scalars().all()


@router.get("/failed", response_model=list[ClassificationOut])
async def failed_classifications(db: AsyncSession = Depends(get_db)):
    """Retrieves all classification records that marked failed=True."""
    result = await db.execute(
        select(Classification).where(Classification.failed == True)
    )
    return result.scalars().all()