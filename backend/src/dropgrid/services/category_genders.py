"""Which account gender sends to each grid category. Database only; no VK calls."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import (
    CategoryGenderInput,
    CategoryGenderRead,
    GridCategoryGendersRead,
)
from dropgrid.db.models import Grid, GridCategoryGender, GridCommunity
from dropgrid.domain.enums import CategoryGender
from dropgrid.services.catalog import InvalidGridError, NotFoundError, get_entity
from dropgrid.services.workflow import grid_categories

# Storage key of communities imported before any category heading.
NO_CATEGORY = ""


@dataclass(frozen=True)
class Placement:
    category: str | None
    # None: the operator has not put this category into any column yet.
    gender: CategoryGender | None


UNPLACED = Placement(None, None)


async def _saved(session: AsyncSession, grid_id: UUID) -> dict[str | None, GridCategoryGender]:
    rows = await session.scalars(
        select(GridCategoryGender).where(GridCategoryGender.grid_id == grid_id)
    )
    return {row.category or None: row for row in rows}


async def placements(session: AsyncSession, grid_id: UUID) -> dict[UUID, Placement]:
    """Grid category and its saved gender for every community of the grid."""
    genders = {category: row.gender for category, row in (await _saved(session, grid_id)).items()}
    rows = await session.execute(
        select(GridCommunity.community_id, GridCommunity.category).where(
            GridCommunity.grid_id == grid_id
        )
    )
    return {
        community_id: Placement(category, genders.get(category)) for community_id, category in rows
    }


async def _suggestions(
    session: AsyncSession, grid_id: UUID, names: list[str]
) -> dict[str, CategoryGender]:
    if not names:
        return {}
    rows = await session.execute(
        select(GridCategoryGender.category, GridCategoryGender.gender)
        .where(GridCategoryGender.grid_id != grid_id, GridCategoryGender.category != NO_CATEGORY)
        .order_by(GridCategoryGender.updated_at.desc())
    )
    # Case-folded in Python: PostgreSQL lower() ignores Cyrillic under a C locale.
    latest: dict[str, CategoryGender] = {}
    for category, gender in rows.tuples():
        latest.setdefault(category.casefold(), gender)
    return {name: latest[name.casefold()] for name in names if name.casefold() in latest}


async def read(session: AsyncSession, grid_id: UUID) -> GridCategoryGendersRead:
    await get_entity(session, Grid, grid_id)
    counts = await grid_categories(session, grid_id)
    saved = await _saved(session, grid_id)
    suggested = await _suggestions(
        session, grid_id, [c.category for c in counts if c.category and c.category not in saved]
    )
    present = [saved[c.category] for c in counts if c.category in saved]
    return GridCategoryGendersRead(
        grid_id=grid_id,
        categories=[
            CategoryGenderRead(
                category=c.category,
                count=c.count,
                gender=saved[c.category].gender if c.category in saved else None,
                suggested_gender=suggested.get(c.category) if c.category else None,
            )
            for c in counts
        ],
        complete=bool(counts) and len(present) == len(counts),
        updated_at=max((row.updated_at for row in present), default=None),
    )


async def save(
    session: AsyncSession, grid_id: UUID, items: list[CategoryGenderInput]
) -> GridCategoryGendersRead:
    """Replace the whole distribution; every current grid category must be placed once."""
    # The grid row lock serializes concurrent saves of the same distribution.
    if await session.get(Grid, grid_id, with_for_update=True) is None:
        raise NotFoundError("Grid not found")
    present = {c.category for c in await grid_categories(session, grid_id)}
    chosen = {item.category: item.gender for item in items}
    if len(chosen) != len(items):
        raise InvalidGridError("Each category can be placed into one column only")
    if chosen.keys() - present:
        raise InvalidGridError("Category is not part of this grid")
    if present - chosen.keys():
        raise InvalidGridError("Place every grid category into a column")
    await session.execute(delete(GridCategoryGender).where(GridCategoryGender.grid_id == grid_id))
    session.add_all(
        GridCategoryGender(
            grid_id=grid_id,
            category=category if category is not None else NO_CATEGORY,
            gender=gender,
        )
        for category, gender in chosen.items()
    )
    await session.flush()
    return await read(session, grid_id)
