"""Optional Supabase Auth JWT verification.

Set REQUIRE_AUTH=true in production when the frontend is using Supabase Auth.
The dependency is deliberately optional during migration so existing clients can
be moved without a flag-day API break.
"""
from __future__ import annotations

import os
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def _required() -> bool:
    return os.getenv("REQUIRE_AUTH", "false").lower() == "true"


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any] | None:
    if credentials is None:
        if _required():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        return None

    token = credentials.credentials
    secret = os.getenv("SUPABASE_JWT_SECRET")
    try:
        if secret:
            return jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})

        # Supabase projects using asymmetric signing keys can expose a JWKS URL.
        jwks_url = os.getenv("SUPABASE_JWKS_URL")
        if not jwks_url:
            base = os.getenv("SUPABASE_URL", "").rstrip("/")
            jwks_url = f"{base}/auth/v1/.well-known/jwks.json" if base else ""
        if not jwks_url:
            raise ValueError("SUPABASE_JWKS_URL or SUPABASE_URL is required")

        from jwt import PyJWKClient
        signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=["RS256", "ES256"], options={"verify_aud": False})
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token") from exc
