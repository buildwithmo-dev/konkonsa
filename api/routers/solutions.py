import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models import Solution, PainPoint, Trend, SolutionStatus
from schemas import SolutionOut, SolutionUpdate, SolutionGenerateRequest, MessageResponse
from services.llm import call_llm, LLMError, LLMTransientError
from services.rate_limit import rate_limiter

router = APIRouter(prefix="/solutions", tags=["Solutions"])

# Solution generation/expansion/validation each cost one Groq call and produce
# LLM-written content someone will read — a tighter default than /classify.
SOLUTIONS_RATE_LIMIT = int(os.getenv("SOLUTIONS_RATE_LIMIT", "15"))
SOLUTIONS_RATE_WINDOW_SECONDS = int(os.getenv("SOLUTIONS_RATE_WINDOW_SECONDS", "3600"))

_solutions_limit = Depends(
    rate_limiter("solutions_llm", limit=SOLUTIONS_RATE_LIMIT, window_seconds=SOLUTIONS_RATE_WINDOW_SECONDS)
)


@router.post("/generate", response_model=list[SolutionOut], dependencies=[_solutions_limit])
async def generate_solutions(
    payload: SolutionGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    context = ""

    if payload.pain_point_id:
        result = await db.execute(select(PainPoint).where(PainPoint.id == payload.pain_point_id))
        pp = result.scalar_one_or_none()
        if not pp:
            raise HTTPException(status_code=404, detail="Pain point not found")
        context = f"Pain Point: {pp.title}\nDescription: {pp.description}\nAudience: {pp.audience}\nSeverity: {pp.severity}/10"

    elif payload.trend_id:
        result = await db.execute(select(Trend).where(Trend.id == payload.trend_id))
        trend = result.scalar_one_or_none()
        if not trend:
            raise HTTPException(status_code=404, detail="Trend not found")
        context = f"Trend: {trend.title}\nDescription: {trend.description}\nAudience: {trend.audience}"

    else:
        raise HTTPException(status_code=400, detail="Provide pain_point_id or trend_id")

    prompt = f"""
You are a startup idea generator. Based on the following context, generate {payload.count} distinct solution ideas.

{context}

Return ONLY a valid JSON array with this structure (no markdown, no preamble):
[
  {{
    "title": "Solution name",
    "description": "What it is and how it solves the problem",
    "business_model": "How it makes money",
    "target_audience": "Who it's for",
    "risks": "Key risks or challenges"
  }}
]
"""
    try:
        ideas = await call_llm(prompt, max_tokens=1500, as_json=True)
    except LLMTransientError as e:
        raise HTTPException(status_code=503, detail=f"Model temporarily unavailable: {e}") from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"Solution generation failed: {e}") from e

    if not isinstance(ideas, list):
        raise HTTPException(status_code=502, detail="Model did not return a JSON array of ideas")

    solutions = []
    for idea in ideas:
        sol = Solution(
            pain_point_id=payload.pain_point_id,
            trend_id=payload.trend_id,
            title=idea.get("title", ""),
            description=idea.get("description"),
            business_model=idea.get("business_model"),
            target_audience=idea.get("target_audience"),
            risks=idea.get("risks"),
        )
        db.add(sol)
        solutions.append(sol)

    await db.commit()
    for s in solutions:
        await db.refresh(s)
    return solutions


@router.get("", response_model=list[SolutionOut])
async def list_solutions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Solution).order_by(Solution.created_at.desc()))
    return result.scalars().all()


@router.get("/saved", response_model=list[SolutionOut])
async def saved_solutions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Solution).where(Solution.status == SolutionStatus.saved)
    )
    return result.scalars().all()


@router.get("/{solution_id}", response_model=SolutionOut)
async def get_solution(solution_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="Solution not found")
    return sol


@router.put("/{solution_id}", response_model=SolutionOut)
async def update_solution(
    solution_id: str, payload: SolutionUpdate, db: AsyncSession = Depends(get_db)
):
    """
    Partial update — title/description/business_model/target_audience/risks/status.
    This is the route the frontend's Save/Dismiss actions hit (lib/api.ts: setSolutionStatus).
    No LLM call here, so no rate limit.
    """
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="Solution not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(sol, k, v)
    await db.commit()
    await db.refresh(sol)
    return sol


@router.delete("/{solution_id}", response_model=MessageResponse)
async def delete_solution(solution_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="Solution not found")
    await db.delete(sol)
    await db.commit()
    return {"message": "Solution deleted"}


@router.post("/{solution_id}/save", response_model=SolutionOut)
async def save_solution(solution_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="Solution not found")
    sol.status = SolutionStatus.saved
    await db.commit()
    await db.refresh(sol)
    return sol


@router.post("/{solution_id}/expand", response_model=SolutionOut, dependencies=[_solutions_limit])
async def expand_solution(solution_id: str, db: AsyncSession = Depends(get_db)):
    """Deep-dive: adds business model and risk analysis via the LLM."""
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="Solution not found")

    prompt = f"""
Expand the following solution idea with a detailed deep-dive. Return ONLY valid JSON.

Solution: {sol.title}
Description: {sol.description}

Return this JSON:
{{
  "business_model": "Detailed revenue model (subscriptions, freemium, marketplace, etc.)",
  "target_audience": "Specific audience segments with demographics",
  "risks": "Top 3 risks with mitigation strategies"
}}
"""
    try:
        expanded = await call_llm(prompt, max_tokens=800, as_json=True)
    except LLMTransientError as e:
        raise HTTPException(status_code=503, detail=f"Model temporarily unavailable: {e}") from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"Expansion failed: {e}") from e

    sol.business_model = expanded.get("business_model", sol.business_model)
    sol.target_audience = expanded.get("target_audience", sol.target_audience)
    sol.risks = expanded.get("risks", sol.risks)

    await db.commit()
    await db.refresh(sol)
    return sol


@router.post("/{solution_id}/validate", response_model=SolutionOut, dependencies=[_solutions_limit])
async def validate_solution(solution_id: str, db: AsyncSession = Depends(get_db)):
    """Ask the LLM to stress-test and critique the idea."""
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="Solution not found")

    prompt = f"""
Act as a critical venture capitalist. Stress-test this startup idea and return ONLY a plain text critique.

Title: {sol.title}
Description: {sol.description}
Business Model: {sol.business_model}
Target Audience: {sol.target_audience}

Cover: market size reality check, competition, why it could fail, and what would make it succeed.
Keep it under 300 words, be direct and honest.
"""
    try:
        critique = await call_llm(prompt, max_tokens=500)
    except LLMTransientError as e:
        raise HTTPException(status_code=503, detail=f"Model temporarily unavailable: {e}") from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"Validation failed: {e}") from e

    sol.validation_notes = critique
    await db.commit()
    await db.refresh(sol)
    return sol