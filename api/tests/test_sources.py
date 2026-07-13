import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_sources_empty(client: AsyncClient):
    response = await client.get("/sources")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_source(client: AsyncClient):
    payload = {
        "name": "Test Reddit",
        "type": "reddit",
        "config": {"subreddits": ["startupideas"], "mode": "hot"},
        "fetch_interval_minutes": 30,
    }
    response = await client.post("/sources", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Reddit"
    assert data["type"] == "reddit"
    assert data["is_active"] is True
    return data["id"]


@pytest.mark.asyncio
async def test_create_and_list_source(client: AsyncClient):
    await client.post("/sources", json={
        "name": "HN Source",
        "type": "hackernews",
        "config": {},
        "fetch_interval_minutes": 60,
    })
    response = await client.get("/sources")
    assert response.status_code == 200
    assert len(response.json()) >= 1


@pytest.mark.asyncio
async def test_update_source(client: AsyncClient):
    create = await client.post("/sources", json={
        "name": "To Update",
        "type": "reddit",
        "config": {},
        "fetch_interval_minutes": 30,
    })
    source_id = create.json()["id"]

    response = await client.put(f"/sources/{source_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_source(client: AsyncClient):
    create = await client.post("/sources", json={
        "name": "To Delete",
        "type": "reddit",
        "config": {},
        "fetch_interval_minutes": 30,
    })
    source_id = create.json()["id"]

    response = await client.delete(f"/sources/{source_id}")
    assert response.status_code == 200

    response = await client.get("/sources")
    ids = [s["id"] for s in response.json()]
    assert source_id not in ids


@pytest.mark.asyncio
async def test_get_source_status(client: AsyncClient):
    create = await client.post("/sources", json={
        "name": "Status Test",
        "type": "google_trends",
        "config": {},
        "fetch_interval_minutes": 360,
    })
    source_id = create.json()["id"]

    response = await client.get(f"/sources/{source_id}/status")
    assert response.status_code == 200
    assert response.json()["id"] == source_id


@pytest.mark.asyncio
async def test_trigger_source(client: AsyncClient):
    create = await client.post("/sources", json={
        "name": "Trigger Test",
        "type": "reddit",
        "config": {},
        "fetch_interval_minutes": 30,
    })
    source_id = create.json()["id"]

    response = await client.post(f"/sources/{source_id}/trigger")
    assert response.status_code == 200
    assert "triggered" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_source_not_found(client: AsyncClient):
    response = await client.get("/sources/nonexistent-id/status")
    assert response.status_code == 404
