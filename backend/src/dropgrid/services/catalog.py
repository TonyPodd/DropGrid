from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import (
    AccountCreate,
    AccountPatch,
    GridCommunityPatch,
    GridCommunityRead,
    GridImport,
)
from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    Grid,
    GridCommunity,
    MediaAsset,
    PhotoReviewBatch,
    PhotoReviewer,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.domain.grid_parser import ParseGridResult, parse_grid

type CatalogModel = (
    Account
    | Campaign
    | Community
    | Grid
    | MediaAsset
    | Submission
    | PhotoReviewer
    | PhotoReviewBatch
    | PhotoSelectionSession
)


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class InvalidGridError(Exception):
    pass


async def get_entity[Model: CatalogModel](
    session: AsyncSession, model: type[Model], entity_id: UUID
) -> Model:
    entity = await session.get(model, entity_id)
    if entity is None:
        raise NotFoundError(f"{model.__name__} not found")
    return entity


async def list_entities[Model: CatalogModel](
    session: AsyncSession, model: type[Model], limit: int, offset: int
) -> Sequence[Model]:
    # UUID ordering is deterministic, including entities with equal timestamps.
    return (
        await session.scalars(select(model).order_by(model.id).limit(limit).offset(offset))
    ).all()


async def create_account(session: AsyncSession, data: AccountCreate) -> Account:
    account = Account(**data.model_dump())
    session.add(account)
    await session.flush()
    return account


async def patch_account(session: AsyncSession, entity_id: UUID, data: AccountPatch) -> Account:
    account = await get_entity(session, Account, entity_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(account, key, value)
    await session.flush()
    return account


async def create_grid(session: AsyncSession, name: str) -> Grid:
    grid = Grid(name=name)
    session.add(grid)
    await session.flush()
    return grid


async def grid_communities(session: AsyncSession, grid_id: UUID) -> Sequence[Community]:
    await get_entity(session, Grid, grid_id)
    return (
        await session.scalars(
            select(Community)
            .join(GridCommunity)
            .where(GridCommunity.grid_id == grid_id)
            .order_by(Community.domain)
        )
    ).all()


async def import_grid(session: AsyncSession, data: GridImport) -> tuple[Grid, ParseGridResult]:
    parsed = parse_grid(data.text)
    if not parsed.items:
        raise InvalidGridError("Grid has no valid communities; use /grids/parse for line errors")
    if data.grid_id:
        grid = await session.get(Grid, data.grid_id, with_for_update=True)
        if grid is None:
            raise NotFoundError("Grid not found")
        grid.name = data.name
    else:
        grid = await create_grid(session, data.name)
    # Sort to give concurrent imports consistent lock ordering.
    for item in sorted(parsed.items, key=lambda item: item.community):
        if item.category is not None and len(item.category) > 200:
            raise InvalidGridError("Category must contain at most 200 characters")
        numeric_id = (
            int(item.community[4:])
            if item.community.startswith("club") and item.community[4:].isdigit()
            else None
        )
        if numeric_id is not None and numeric_id > 2**63 - 1:
            raise InvalidGridError("Numeric group ID exceeds PostgreSQL bigint range")
        # Imported reference survives canonical alias updates. Reimport is additive
        # and preserves deliberate hints on existing per-grid rows.
        community_id = await session.scalar(
            select(GridCommunity.community_id).where(
                GridCommunity.grid_id == grid.id, GridCommunity.source_reference == item.community
            )
        )
        if community_id is None:
            await session.execute(
                insert(Community)
                .values(domain=item.community, category=item.category, vk_group_id=numeric_id)
                .on_conflict_do_nothing(index_elements=[Community.domain])
            )
            community_id = await session.scalar(
                select(Community.id).where(Community.domain == item.community)
            )
        values = dict(category=item.category, comment=item.comment, source_reference=item.community)
        await session.execute(
            insert(GridCommunity)
            .values(grid_id=grid.id, community_id=community_id, **values)
            .on_conflict_do_update(
                index_elements=[GridCommunity.grid_id, GridCommunity.community_id], set_=values
            )
        )
    await session.flush()
    return grid, parsed


async def patch_grid_community(
    session: AsyncSession, grid_id: UUID, community_id: UUID, data: GridCommunityPatch
) -> GridCommunityRead:
    relation = await session.get(GridCommunity, (grid_id, community_id), with_for_update=True)
    if relation is None:
        raise NotFoundError("Community is not in grid")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(relation, key, value or None)
    await session.flush()
    community = await get_entity(session, Community, community_id)
    return GridCommunityRead.model_validate(community).model_copy(
        update={
            "category": relation.category,
            "comment": relation.comment,
            "content_hint": relation.content_hint,
        }
    )
