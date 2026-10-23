"""Instagram profiles listed as photo sources: add by links, inspect, refresh, remove."""

import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field
from sqlalchemy import select

from dropgrid.api.access import remote_user
from dropgrid.api.dependencies import Session
from dropgrid.api.schemas import Input, Output
from dropgrid.db.models import InstagramProfile
from dropgrid.integrations.instagram import parse_username
from dropgrid.services.catalog import get_entity

router = APIRouter(prefix="/api/v1")


class ProfilesInput(Input):
    # Links, @usernames or usernames separated by new lines, spaces or commas.
    links: str = Field(min_length=1, max_length=20_000)


class ProfileRead(Output):
    id: UUID
    username: str
    url: str
    enabled: bool
    status: str
    error_code: str | None
    photo_count: int
    last_synced_at: datetime | None
    added_by: str | None
    created_at: datetime


def profile_read(profile: InstagramProfile) -> ProfileRead:
    return ProfileRead(
        id=profile.id,
        username=profile.username,
        url=f"https://www.instagram.com/{profile.username}/",
        enabled=profile.enabled,
        status=profile.status,
        error_code=profile.error_code,
        photo_count=profile.photo_count,
        last_synced_at=profile.last_synced_at,
        added_by=profile.added_by,
        created_at=profile.created_at,
    )


@router.get("/instagram-profiles")
async def profiles(session: Session, request: Request) -> dict[str, object]:
    rows = (
        await session.scalars(
            select(InstagramProfile).order_by(
                InstagramProfile.created_at.desc(), InstagramProfile.id
            )
        )
    ).all()
    engine = request.app.state.photo_engine
    return {
        # Without the token and our Business account id nothing can be read.
        "configured": engine.instagram.configured,
        "visual": engine.embedder is not None,
        "profiles": [profile_read(p) for p in rows],
    }


@router.post("/instagram-profiles")
async def add_profiles(
    data: ProfilesInput, session: Session, request: Request
) -> dict[str, list[str]]:
    added, existing, invalid = [], [], []
    for value in dict.fromkeys(v for v in re.split(r"[\s,;]+", data.links) if v):
        username = parse_username(value)
        if username is None:
            invalid.append(value[:200])
            continue
        if username in added or await session.scalar(
            select(InstagramProfile.id).where(InstagramProfile.username == username)
        ):
            existing.append(username)
            continue
        session.add(InstagramProfile(username=username, added_by=remote_user(request)))
        added.append(username)
    await session.flush()
    return {"added": added, "existing": existing, "invalid": invalid}


@router.post("/instagram-profiles/{profile_id}/sync")
async def refresh_profile(profile_id: UUID, session: Session) -> ProfileRead:
    profile = await get_entity(session, InstagramProfile, profile_id)
    # Read again on the next worker cycle.
    profile.last_synced_at = None
    profile.status, profile.error_code = "pending", None
    await session.flush()
    return profile_read(profile)


@router.delete("/instagram-profiles/{profile_id}", status_code=204)
async def remove_profile(profile_id: UUID, session: Session) -> Response:
    # Its photos stop being candidates; photos already proposed keep their history.
    await session.delete(await get_entity(session, InstagramProfile, profile_id))
    await session.flush()
    return Response(status_code=204)
