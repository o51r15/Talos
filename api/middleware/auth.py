"""
Authentication middleware stub.
When auth.enabled = false (default), this is a pass-through.
When auth.enabled = true, enforces Bearer token / session auth.
Full implementation is a future phase — hooks are here so it's not a retrofit.
"""

from __future__ import annotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import get_config


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cfg = get_config()

        # Auth disabled — pass through
        if not cfg.auth.enabled:
            return await call_next(request)

        # Exempt paths
        exempt = {"/", "/health", "/static", "/api/docs", "/api/redoc", "/api/openapi.json"}
        if any(request.url.path.startswith(p) for p in exempt):
            return await call_next(request)

        # ── Token check (stub — replace with real JWT/session logic) ──────────
        token = request.headers.get("Authorization", "")
        if not token.startswith("Bearer ") or not _validate_token(token[7:]):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


def _validate_token(token: str) -> bool:
    """Stub — always returns False when auth is enabled but not configured."""
    # TODO: implement JWT validation in auth phase
    return False
