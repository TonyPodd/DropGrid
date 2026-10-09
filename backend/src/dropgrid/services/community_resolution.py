"""Typed read-only batch resolution, short transactions, no status deactivation on errors."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import Community, Grid, GridCommunity, utcnow
from dropgrid.domain.grid_parser import normalize_vk_community_reference
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.models import CommunityResolution
from dropgrid.services.catalog import get_entity
from dropgrid.services.vk_accounts import enabled_account


async def apply_resolution(
    session: AsyncSession, community_id: UUID, result: CommunityResolution
) -> None:
    row = await session.get(Community, community_id, with_for_update=True)
    if row is None:
        return
    row.resolution_status = result.status
    row.resolution_error_code = result.error_code
    row.resolution_checked_at = utcnow()
    group = result.group
    if group is None:
        return
    row.vk_group_id = group.id
    if group.name:
        row.name = group.name[:200]
    if group.screen_name:
        try:
            canonical = normalize_vk_community_reference(group.screen_name)
        except ValueError:
            row.resolution_status = "unresolved"
            return
        conflict = await session.scalar(
            select(Community.id).where(Community.domain == canonical, Community.id != row.id)
        )
        # Keep each grid's imported relation and source identity intact. A canonical
        # alias already represented locally is not a reason to link the wrong row.
        if conflict is None:
            row.domain = canonical


async def resolve_grid_chunk(
    sessions: async_sessionmaker[AsyncSession],
    client: VKClient,
    tokens: TokenProvider,
    grid_id: UUID,
    account_id: UUID,
    offset: int = 0,
    limit: int = 25,
) -> list[CommunityResolution]:
    if not 1 <= limit <= 25 or offset < 0:
        raise ValueError("Invalid resolution chunk")
    async with sessions() as session:
        await get_entity(session, Grid, grid_id)
        await enabled_account(session, account_id)
        rows = (
            await session.execute(
                select(Community.id, Community.domain)
                .join(GridCommunity)
                .where(GridCommunity.grid_id == grid_id)
                .order_by(Community.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    if not rows:
        return []
    token = await tokens.get_token(account_id)
    results = await client.resolve_communities(
        [row.domain for row in rows], access_token=token, account_id=account_id
    )
    async with sessions() as session, session.begin():
        for row, result in zip(rows, results, strict=True):
            await apply_resolution(session, row.id, result)
    return results
