import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models import FeedItem, Source, SourceType
from tests.conftest import TestSessionLocal


async def _seed_source(db: AsyncSession) -> str:
    source = Source(name="Test", type=SourceType.reddit, config={})
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source.id


async def _seed_feed_item(db: AsyncSession, source_id: str, external_id: str = "ext1") -> str:
    item = FeedItem(
        source_id=source_id,
        external_id=external_id,
        title="My app keeps crashing and I lose all my data",
        body="This is so frustrating. Has anyone found a fix?",
        score=150,
        comment_count=42,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item.id


@pytest.mark.asyncio
async def test_list_feed_empty(client: AsyncClient):
    response = await client.get("/feed")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_get_feed_item(client: AsyncClient):
    async with TestSessionLocal() as db:
        source_id = await _seed_source(db)
        item_id = await _seed_feed_item(db, source_id, "ext_get_test")

    response = await client.get(f"/feed/{item_id}")
    assert response.status_code == 200
    assert response.json()["id"] == item_id


@pytest.mark.asyncio
async def test_delete_feed_item(client: AsyncClient):
    async with TestSessionLocal() as db:
        source_id = await _seed_source(db)
        item_id = await _seed_feed_item(db, source_id, "ext_delete_test")

    response = await client.delete(f"/feed/{item_id}")
    assert response.status_code == 200

    response = await client.get(f"/feed/{item_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_feed_item_not_found(client: AsyncClient):
    response = await client.get("/feed/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_duplicates(client: AsyncClient):
    async with TestSessionLocal() as db:
        source_id = await _seed_source(db)
        item = FeedItem(
            source_id=source_id,
            external_id="dup_ext",
            title="Duplicate post",
            is_duplicate=True,
        )
        db.add(item)
        await db.commit()

    response = await client.get("/feed/duplicates")
    assert response.status_code == 200
    assert any(i["is_duplicate"] for i in response.json())


@pytest.mark.asyncio
async def test_feed_pagination(client: AsyncClient):
    response = await client.get("/feed?page=1&page_size=5")
    assert response.status_code == 200
    assert len(response.json()) <= 5
