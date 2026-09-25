import pytest
from httpx import AsyncClient

from models import PainPoint, Trend, ItemType
from tests.conftest import TestSessionLocal


async def _seed_null_keyword_records() -> tuple[str, str]:
    async with TestSessionLocal() as db:
        trend = Trend(
            title="Legacy trend with null keywords",
            category=ItemType.trend,
            score=9.0,
            keywords=None,
        )

        pain_point = PainPoint(
            title="Legacy pain point with null keywords",
            severity=8.0,
            keywords=None,
            is_dismissed=False,
        )

        db.add_all([trend, pain_point])
        await db.commit()

        await db.refresh(trend)
        await db.refresh(pain_point)

        return trend.id, pain_point.id


@pytest.mark.asyncio
async def test_top_trends_normalizes_null_keywords(client: AsyncClient):
    trend_id, _ = await _seed_null_keyword_records()

    response = await client.get("/trends/top?n=50")

    assert response.status_code == 200

    trend = next(
        item
        for item in response.json()
        if item["id"] == trend_id
    )

    assert trend["keywords"] == []


@pytest.mark.asyncio
async def test_top_pain_points_normalizes_null_keywords(client: AsyncClient):
    _, pain_point_id = await _seed_null_keyword_records()

    response = await client.get("/painpoints/top?n=50")

    assert response.status_code == 200

    pain_point = next(
        item
        for item in response.json()
        if item["id"] == pain_point_id
    )

    assert pain_point["keywords"] == []


@pytest.mark.asyncio
async def test_configured_cors_origin_is_returned(client: AsyncClient):
    response = await client.get(
        "/healthz",
        headers={
            "Origin": "https://konkonsa-frontend-lwf8.vercel.app"
        },
    )

    assert response.status_code == 200

    assert (
        response.headers["access-control-allow-origin"]
        == "https://konkonsa-frontend-lwf8.vercel.app"
    )