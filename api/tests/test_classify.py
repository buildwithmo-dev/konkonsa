import pytest
import json
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import FeedItem, Source, SourceType, Classification, ItemType
from tests.conftest import TestSessionLocal

MOCK_CLASSIFICATION = {
    "item_type": "pain_point",
    "topic": "App crashes on save",
    "summary": "Users are losing data due to frequent app crashes during save operations.",
    "audience": "Mobile app users",
    "severity": 8.5,
    "keywords": ["crash", "data loss", "save"],
    "sentiment": "negative",
}


async def _seed_source_and_item(db: AsyncSession, suffix: str = "") -> tuple[str, str]:
    source = Source(name=f"Test{suffix}", type=SourceType.reddit, config={})
    db.add(source)
    await db.commit()
    await db.refresh(source)

    item = FeedItem(
        source_id=source.id,
        external_id=f"ext_cls_{suffix}",
        title="App crashes every time I try to save",
        body="I've lost my work 3 times today. This is unacceptable.",
        score=200,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return source.id, item.id


@pytest.mark.asyncio
async def test_classify_single(client: AsyncClient):
    with patch("routers.classify.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = MOCK_CLASSIFICATION
        response = await client.post("/classify", json={
            "text": "My app crashes every time I save. Data is lost.",
        })
    assert response.status_code == 200
    data = response.json()
    assert data["item_type"] == "pain_point"
    assert data["severity"] == 8.5
    assert "crash" in data["keywords"]


@pytest.mark.asyncio
async def test_classify_batch(client: AsyncClient):
    async with TestSessionLocal() as db:
        _, item_id = await _seed_source_and_item(db, "batch1")

    with patch("routers.classify.call_claude", new_callable=AsyncMock) as mock_claude:
        mock_claude.return_value = MOCK_CLASSIFICATION
        response = await client.post("/classify/batch", json={
            "feed_item_ids": [item_id],
        })
    assert response.status_code == 200
    assert "started" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_classification_queue(client: AsyncClient):
    async with TestSessionLocal() as db:
        _, item_id = await _seed_source_and_item(db, "queue1")

    response = await client.get("/classify/queue")
    assert response.status_code == 200
    assert item_id in response.json()


@pytest.mark.asyncio
async def test_failed_classifications(client: AsyncClient):
    async with TestSessionLocal() as db:
        _, item_id = await _seed_source_and_item(db, "fail1")
        cls = Classification(
            feed_item_id=item_id,
            failed=True,
            error="Claude API timeout",
        )
        db.add(cls)
        await db.commit()

    response = await client.get("/classify/failed")
    assert response.status_code == 200
    assert any(c["failed"] for c in response.json())


@pytest.mark.asyncio
async def test_retry_classification(client: AsyncClient):
    async with TestSessionLocal() as db:
        _, item_id = await _seed_source_and_item(db, "retry1")
        cls = Classification(feed_item_id=item_id, failed=True, error="Timeout")
        db.add(cls)
        await db.commit()

    with patch("routers.classify.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = MOCK_CLASSIFICATION
        response = await client.post(f"/classify/retry/{item_id}")
    assert response.status_code == 200
    assert response.json()["failed"] is False
