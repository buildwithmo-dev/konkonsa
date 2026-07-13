import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/settings/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_version(client: AsyncClient):
    response = await client.get("/settings/version")
    assert response.status_code == 200
    assert "version" in response.json()


@pytest.mark.asyncio
async def test_analytics_summary(client: AsyncClient):
    response = await client.get("/analytics/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_feed_items" in data
    assert "total_trends" in data
    assert "total_pain_points" in data
    assert "total_solutions" in data


@pytest.mark.asyncio
async def test_analytics_categories(client: AsyncClient):
    response = await client.get("/analytics/categories")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_analytics_sentiment(client: AsyncClient):
    response = await client.get("/analytics/sentiment?days=7")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_analytics_sources(client: AsyncClient):
    response = await client.get("/analytics/sources")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_search_empty(client: AsyncClient):
    response = await client.get("/search?q=nonexistentterm12345")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_search_requires_query(client: AsyncClient):
    response = await client.get("/search")
    assert response.status_code == 422   # validation error — q is required


@pytest.mark.asyncio
async def test_clusters_list(client: AsyncClient):
    response = await client.get("/clusters")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_alerts_crud(client: AsyncClient):
    # Create
    response = await client.post("/alerts", json={
        "name": "Test Alert",
        "condition": {"type": "keyword_spike", "value": "AI", "threshold": 5, "window_hours": 1},
        "notification_channel": "log",
    })
    assert response.status_code == 200
    alert_id = response.json()["id"]

    # List
    response = await client.get("/alerts")
    assert any(a["id"] == alert_id for a in response.json())

    # Update
    response = await client.put(f"/alerts/{alert_id}", json={"name": "Updated Alert"})
    assert response.json()["name"] == "Updated Alert"

    # Delete
    response = await client.delete(f"/alerts/{alert_id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_jobs_list(client: AsyncClient):
    response = await client.get("/jobs")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_settings_get(client: AsyncClient):
    response = await client.get("/settings")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_settings_update(client: AsyncClient):
    response = await client.put("/settings/test_key", json={"value": "test_value"})
    assert response.status_code == 200
    assert "updated" in response.json()["message"]
