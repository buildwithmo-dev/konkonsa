"""
Lightweight in-process rate limiting for LLM-cost-incurring endpoints
(classification, solution generation/expansion/validation — anything that
spends a Groq API call per request).

Render's `konkonsa-api` service runs as a single web dyno by default, so an
in-memory sliding window is a legitimate limiter today: there's exactly one
process holding the counters, so nothing is lost or double-counted.

This stops being sufficient the moment you scale `konkonsa-api` to more than
one instance — each instance would track its own counts with no coordination
between them, so the effective limit becomes `limit x instance_count`. If you
ever scale horizontally, move this state to a shared store (a Postgres row
updated atomically, or Redis) instead of the module-level dict below. Flagging
that now so it isn't a surprise later; not solving it preemptively for
infrastructure you don't have yet.

Identity: prefers the authenticated Supabase user id (via get_current_user)
when REQUIRE_AUTH is on, falls back to client IP otherwise. This also means
using this dependency on a route gives you REQUIRE_AUTH enforcement "for
free" the moment you turn that flag on — no separate auth dependency needed.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Depends, HTTPException, Request, status

from auth import get_current_user

# bucket_name -> identity -> deque of unix-monotonic timestamps of recent hits.
# Module-level dict = per-process state. See the caveat in the docstring above.
_buckets: Dict[str, Dict[str, Deque[float]]] = defaultdict(lambda: defaultdict(deque))


def _client_identity(request: Request, user: Optional[dict]) -> str:
    """Prefer the authenticated Supabase user id; fall back to client IP."""
    if user and isinstance(user, dict):
        sub = user.get("sub")
        if sub:
            return f"user:{sub}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


def rate_limiter(bucket: str, limit: int, window_seconds: int):
    """
    FastAPI dependency factory.

    Usage:
        @router.post("/generate", dependencies=[Depends(rate_limiter("solutions_generate", limit=15, window_seconds=3600))])

    Raises 429 once `limit` requests have been made by the same identity
    within the trailing `window_seconds`. Also enforces REQUIRE_AUTH's
    existing semantics via get_current_user (401 if a token is required and
    missing/invalid; no-op if REQUIRE_AUTH=false).
    """

    async def _check(request: Request, user: Optional[dict] = Depends(get_current_user)) -> None:
        identity = _client_identity(request, user)
        now = time.monotonic()
        window_start = now - window_seconds

        history = _buckets[bucket][identity]
        while history and history[0] < window_start:
            history.popleft()

        if len(history) >= limit:
            retry_after = max(1, int(window_seconds - (now - history[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for '{bucket}'. Try again in ~{retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

        history.append(now)

    return _check