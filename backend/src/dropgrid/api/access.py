"""Fail-closed identity from an authenticated, secret-bearing internal proxy."""

import secrets
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from dropgrid.config import Settings
from dropgrid.db.models import PhotoReviewer


class ProxyAccess(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings
        if (
            settings.trusted_proxy_enabled
            and len(settings.trusted_proxy_key.get_secret_value()) < 32
        ):
            raise ValueError("Trusted proxy key must contain at least 32 characters")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.settings.trusted_proxy_enabled or request.url.path == "/health":
            return await call_next(request)
        expected = self.settings.trusted_proxy_key.get_secret_value()
        received = request.headers.get("x-dropgrid-proxy-key", "")
        actor = request.headers.get("x-dropgrid-remote-user", "")
        if not secrets.compare_digest(received, expected) or actor not in {"tony", "tima"}:
            return JSONResponse({"detail": "Authenticated proxy required"}, status_code=401)
        request.state.remote_user = actor
        return await call_next(request)


def owner_token_access(request: Request, settings: Settings) -> None:
    if settings.app_env != "development" and getattr(request.state, "remote_user", None) not in {
        "tony",
        "tima",
    }:
        raise HTTPException(403, "Authenticated owner required")


async def proxy_reviewer(request: Request, session: AsyncSession) -> PhotoReviewer | None:
    actor = getattr(request.state, "remote_user", None)
    if not actor:
        return None
    reviewer = await session.scalar(
        select(PhotoReviewer).where(
            PhotoReviewer.display_name == {"tony": "Tony", "tima": "Tima"}[actor]
        )
    )
    if not reviewer:
        raise HTTPException(409, "Reviewer identity not configured")
    return reviewer


async def check_reviewer(request: Request, session: AsyncSession, reviewer_id: UUID | None) -> None:
    actor = await proxy_reviewer(request, session)
    if actor and reviewer_id is not None and actor.id != reviewer_id:
        raise HTTPException(403, "Reviewer identity does not match authenticated user")
