import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from models import PainPoint, Trend, ItemType, Solution, SolutionStatus
from tests.conftest import TestSessionLocal

MOCK_SOLUTIONS = [
    {
        "title": "CrashGuard Auto-Save",
        "description": "Background auto-save with conflict resolution",
        "business_model": "SaaS $9/mo per user",
        "target_audience": "Power users of productivity apps",
        "risks": "High dev cost, competitive market",
    }
]

MOCK_EXPAND = {
    "business_model": "Freemium: free for 1 project, $9/mo for unlimited",
    "target_audience": "Freelancers and remote teams aged 25-45",
    "risks": "1) Competition from incumbents - mitigate by niche focus; 2) Low ARPU - mitigate with upsells",
}


async def _seed_pain_point() -> str:
    async with TestSessionLocal() as db:
        pp = PainPoint(
            title="App data loss on crash",
            description="Users lose work when app crashes",
            audience="Mobile users",
            severity=8.5,
        )
        db.add(pp)
        await db.commit()
        await db.refresh(pp)
        return pp.id


async def _seed_solution(pain_point_id: str) -> str:
    async with TestSessionLocal() as db:
        sol = Solution(
            pain_point_id=pain_point_id,
            title="AutoSave Pro",
            description="Automatically saves work every 30 seconds",
        )
        db.add(sol)
        await db.commit()
        await db.refresh(sol)
        return sol.id


@pytest.mark.asyncio
async def test_generate_solutions(client: AsyncClient):
    pp_id = await _seed_pain_point()
    with patch("routers.solutions.call_claude", new_callable=AsyncMock) as mock:
        mock.return_value = str(MOCK_SOLUTIONS).replace("'", '"')
        import json
        mock.return_value = json.dumps(MOCK_SOLUTIONS)
        response = await client.post("/solutions/generate", json={
            "pain_point_id": pp_id,
            "count": 1,
        })
    assert response.status_code == 200
    assert len(response.json()) >= 1
    assert response.json()[0]["title"] == "CrashGuard Auto-Save"


@pytest.mark.asyncio
async def test_list_solutions(client: AsyncClient):
    response = await client.get("/solutions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_get_solution(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)

    response = await client.get(f"/solutions/{sol_id}")
    assert response.status_code == 200
    assert response.json()["id"] == sol_id


@pytest.mark.asyncio
async def test_save_solution(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)

    response = await client.post(f"/solutions/{sol_id}/save")
    assert response.status_code == 200
    assert response.json()["status"] == "saved"


@pytest.mark.asyncio
async def test_saved_solutions_list(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)
    await client.post(f"/solutions/{sol_id}/save")

    response = await client.get("/solutions/saved")
    assert response.status_code == 200
    assert any(s["id"] == sol_id for s in response.json())


@pytest.mark.asyncio
async def test_update_solution(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)

    response = await client.put(f"/solutions/{sol_id}", json={"title": "Updated Title"})
    assert response.status_code == 200
    assert response.json()["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_delete_solution(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)

    response = await client.delete(f"/solutions/{sol_id}")
    assert response.status_code == 200

    response = await client.get(f"/solutions/{sol_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_expand_solution(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)

    with patch("routers.solutions.call_claude", new_callable=AsyncMock) as mock:
        import json
        mock.return_value = json.dumps(MOCK_EXPAND)
        response = await client.post(f"/solutions/{sol_id}/expand")
    assert response.status_code == 200
    assert response.json()["business_model"] is not None


@pytest.mark.asyncio
async def test_validate_solution(client: AsyncClient):
    pp_id = await _seed_pain_point()
    sol_id = await _seed_solution(pp_id)

    with patch("routers.solutions.call_claude", new_callable=AsyncMock) as mock:
        mock.return_value = "This idea has merit but faces stiff competition from incumbents..."
        response = await client.post(f"/solutions/{sol_id}/validate")
    assert response.status_code == 200
    assert response.json()["validation_notes"] is not None


@pytest.mark.asyncio
async def test_solution_not_found(client: AsyncClient):
    response = await client.get("/solutions/nonexistent")
    assert response.status_code == 404
