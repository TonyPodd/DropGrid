"""Database-only views for campaign preparation; no VK dependencies or network calls."""

import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import (
    CampaignRead,
    CampaignStats,
    CampaignSummary,
    CategoryCount,
    CommunityRead,
    DashboardRead,
    GridCommunityPage,
    GridDetail,
    GridRead,
    GridSummary,
    SubmissionPage,
    SubmissionRead,
)
from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    Grid,
    GridCommunity,
    MediaAsset,
    Submission,
)
from dropgrid.domain.enums import CampaignStatus, SubmissionStatus
from dropgrid.services.catalog import get_entity


async def grid_categories(session: AsyncSession, grid_id: UUID) -> list[CategoryCount]:
    rows = await session.execute(
        select(GridCommunity.category, func.count())
        .where(GridCommunity.grid_id == grid_id)
        .group_by(GridCommunity.category)
        .order_by(GridCommunity.category.asc().nulls_last())
    )
    return [CategoryCount(category=category, count=count) for category, count in rows]


async def grid_list(session: AsyncSession, limit: int, offset: int) -> list[GridSummary]:
    counts = (
        select(
            GridCommunity.grid_id,
            func.count().label("communities"),
            func.count(func.distinct(GridCommunity.category)).label("categories"),
        )
        .group_by(GridCommunity.grid_id)
        .subquery()
    )
    rows = await session.execute(
        select(Grid, func.coalesce(counts.c.communities, 0), func.coalesce(counts.c.categories, 0))
        .outerjoin(counts, Grid.id == counts.c.grid_id)
        .order_by(Grid.created_at.desc(), Grid.id)
        .limit(limit)
        .offset(offset)
    )
    return [
        GridSummary(
            **GridRead.model_validate(grid).model_dump(),
            community_count=count,
            category_count=categories,
        )
        for grid, count, categories in rows
    ]


async def grid_members(
    session: AsyncSession, grid_id: UUID, page: int, page_size: int
) -> GridCommunityPage:
    await get_entity(session, Grid, grid_id)
    total = await session.scalar(
        select(func.count()).select_from(GridCommunity).where(GridCommunity.grid_id == grid_id)
    )
    rows = await session.execute(
        select(Community, GridCommunity.category)
        .join(GridCommunity)
        .where(GridCommunity.grid_id == grid_id)
        .order_by(Community.domain, Community.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = [
        CommunityRead.model_validate(community).model_copy(update={"category": category})
        for community, category in rows
    ]
    return GridCommunityPage(items=items, total=total or 0, page=page, page_size=page_size)


async def grid_detail(session: AsyncSession, grid_id: UUID, limit: int, offset: int) -> GridDetail:
    grid = await get_entity(session, Grid, grid_id)
    # The detail embeds a bounded first/selected slice for compatibility; UI uses the page endpoint.
    rows = await session.execute(
        select(Community, GridCommunity.category)
        .join(GridCommunity)
        .where(GridCommunity.grid_id == grid_id)
        .order_by(Community.domain, Community.id)
        .limit(limit)
        .offset(offset)
    )
    categories = await grid_categories(session, grid_id)
    return GridDetail(
        **GridRead.model_validate(grid).model_dump(),
        communities=[
            CommunityRead.model_validate(c).model_copy(update={"category": category})
            for c, category in rows
        ],
        community_count=sum(c.count for c in categories),
        categories=categories,
    )


async def campaign_list(
    session: AsyncSession, limit: int, offset: int, campaign_id: UUID | None = None
) -> list[CampaignSummary]:
    members = (
        select(GridCommunity.grid_id, func.count().label("count"))
        .group_by(GridCommunity.grid_id)
        .subquery()
    )
    submissions = (
        select(Submission.campaign_id, func.count().label("count"))
        .group_by(Submission.campaign_id)
        .subquery()
    )
    query = (
        select(
            Campaign,
            Grid.name,
            func.coalesce(members.c.count, 0),
            func.coalesce(submissions.c.count, 0),
        )
        .join(Grid, Grid.id == Campaign.grid_id)
        .outerjoin(members, members.c.grid_id == Campaign.grid_id)
        .outerjoin(submissions, submissions.c.campaign_id == Campaign.id)
        .order_by(Campaign.created_at.desc(), Campaign.id)
        .limit(limit)
        .offset(offset)
    )
    if campaign_id is not None:
        query = query.where(Campaign.id == campaign_id)
    rows = await session.execute(query)
    return [
        CampaignSummary(
            **CampaignRead.model_validate(c).model_dump(),
            grid_name=name,
            community_count=members_count,
            submission_count=submission_count,
        )
        for c, name, members_count, submission_count in rows
    ]


async def dashboard(session: AsyncSession) -> DashboardRead:
    # One statement for catalog counts, one grouped status query, one recent-campaign query.
    counts_row = (
        await session.execute(
            select(
                select(func.count()).select_from(Account).scalar_subquery(),
                select(func.count()).select_from(Community).scalar_subquery(),
                select(func.count()).select_from(Grid).scalar_subquery(),
                select(func.count()).select_from(Campaign).scalar_subquery(),
            )
        )
    ).one()
    statuses = dict.fromkeys(CampaignStatus, 0)
    for status, count in await session.execute(
        select(Campaign.status, func.count()).group_by(Campaign.status)
    ):
        statuses[status] = count
    return DashboardRead(
        counts=dict(
            zip(("accounts", "communities", "grids", "campaigns"), counts_row, strict=True)
        ),
        campaign_statuses=statuses,
        recent_campaigns=await campaign_list(session, 8, 0),
    )


async def campaign_stats(session: AsyncSession, campaign_id: UUID) -> CampaignStats:
    await get_entity(session, Campaign, campaign_id)
    statuses = dict.fromkeys(SubmissionStatus, 0)
    for status, count in await session.execute(
        select(Submission.status, func.count())
        .where(Submission.campaign_id == campaign_id)
        .group_by(Submission.status)
    ):
        statuses[status] = count
    assigned, unique = (
        await session.execute(
            select(
                func.count(Submission.media_asset_id),
                func.count(func.distinct(Submission.media_asset_id)),
            ).where(Submission.campaign_id == campaign_id)
        )
    ).one()
    return CampaignStats(
        total=sum(statuses.values()),
        statuses=statuses,
        media_assigned=assigned,
        media_unique=unique,
    )


def safe_result(code: str | None, message: str | None) -> tuple[str | None, str | None]:
    """Legacy free-form errors may contain credentials: expose only bounded numeric codes.

    Human-readable raw errors are intentionally replaced with a fixed safe message.
    """
    safe_code = (code if re.fullmatch(r"\d{1,6}", code) else "ERROR") if code else None
    return safe_code, "Submission failed; details withheld." if code or message else None


async def submission_list(
    session: AsyncSession,
    campaign_id: UUID,
    page: int,
    page_size: int,
    status: SubmissionStatus | None,
    category: str | None,
) -> SubmissionPage:
    campaign = await get_entity(session, Campaign, campaign_id)
    query = (
        select(Submission, Community, GridCommunity.category, Account.name, MediaAsset.id)
        .join(Community, Community.id == Submission.community_id)
        .join(
            GridCommunity,
            (GridCommunity.community_id == Submission.community_id)
            & (GridCommunity.grid_id == campaign.grid_id),
        )
        .outerjoin(Account, Account.id == Submission.account_id)
        .outerjoin(MediaAsset, MediaAsset.id == Submission.media_asset_id)
        .where(Submission.campaign_id == campaign_id)
    )
    if status is not None:
        query = query.where(Submission.status == status)
    if category is not None:
        query = (
            query.where(GridCommunity.category == category)
            if category
            else query.where(GridCommunity.category.is_(None))
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(
        query.order_by(Community.domain, Submission.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = []
    for submission, community, grid_category, account_name, media_id in rows:
        code, message = safe_result(submission.error_code, submission.error_message)
        url = submission.published_post_url
        if url and not re.fullmatch(r"https://(?:vk\.com|vk\.ru)/wall-?\d+_\d+", url):
            url = None
        items.append(
            SubmissionRead(
                id=submission.id,
                community=CommunityRead.model_validate(community).model_copy(
                    update={"category": grid_category}
                ),
                category=grid_category,
                account_id=submission.account_id,
                account_name=account_name,
                media_asset_id=media_id,
                media_label=f"Asset {media_id}" if media_id else None,
                status=submission.status,
                attempt_count=submission.attempt_count,
                error_code=code,
                error_message=message,
                published_post_url=url,
            )
        )
    return SubmissionPage(items=items, total=total or 0, page=page, page_size=page_size)
