"""Deterministic campaign capacity planning. No network calls or write retries."""

from collections import Counter, deque
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.config import Settings
from dropgrid.db.models import Account, Campaign, CampaignAccount, Community, Submission
from dropgrid.domain.enums import AccountStatus, CategoryGender, GenderTag
from dropgrid.services.catalog import ConflictError, get_entity
from dropgrid.services.category_genders import UNPLACED, Placement

GENDERED = {GenderTag.male, GenderTag.female}
CATEGORY_UNASSIGNED = "category_gender_unassigned"


def required_gender(community: Community, placement: Placement | None = None) -> GenderTag | None:
    """Account gender a target needs; None lets any account send (unisex)."""
    if community.required_gender_tag in GENDERED:
        return community.required_gender_tag
    if placement and placement.gender in {CategoryGender.male, CategoryGender.female}:
        return GenderTag(placement.gender.value)
    return None


def compatible(required: GenderTag | None, account: Account) -> bool:
    return required is None or required == account.gender_tag


def distribute(
    rows: list[tuple[Submission, Community]],
    accounts: list[tuple[Account, int, int]],
    placements: Mapping[UUID, Placement],
) -> tuple[dict[UUID, UUID], dict[UUID, str]]:
    """Fill accounts one at a time in pool order: own-gender categories, then unisex.

    The next account only receives what earlier accounts could not take. Insertion
    order of the returned allocation is the campaign send order.
    """
    assigned: dict[UUID, UUID] = {}
    reasons: dict[UUID, str] = {}
    queues: dict[GenderTag | None, deque[UUID]] = {g: deque() for g in (*GENDERED, None)}

    def order(row: tuple[Submission, Community]) -> tuple[bool, str, str, str]:
        category = placements.get(row[1].id, UNPLACED).category
        return category is None, category or "", row[1].domain, str(row[0].id)

    for row, community in sorted(rows, key=order):
        placement = placements.get(community.id, UNPLACED)
        if placement.gender is None and community.required_gender_tag not in GENDERED:
            reasons[row.id] = CATEGORY_UNASSIGNED
            continue
        queues[required_gender(community, placement)].append(row.id)
    usable = sorted(
        (
            (a, quota, priority)
            for a, quota, priority in accounts
            if a.status == AccountStatus.active and a.vk_user_id and a.encrypted_access_token
        ),
        key=lambda item: (item[2], str(item[0].id)),
    )
    counts: Counter[UUID] = Counter()
    for account, quota, _ in usable:
        own = queues[account.gender_tag] if account.gender_tag in GENDERED else deque()
        for queue in (own, queues[None]):
            while queue and counts[account.id] < quota:
                assigned[queue.popleft()] = account.id
                counts[account.id] += 1
    for gender, queue in queues.items():
        possible = any(compatible(gender, a) for a, _, _ in usable)
        for row_id in queue:
            reasons[row_id] = (
                "account_capacity_exhausted" if possible else "account_gender_mismatch"
            )
    return assigned, reasons


async def pool(
    session: AsyncSession, campaign_id: UUID, settings: Settings, selected: list[UUID] | None = None
) -> list[tuple[Account, int, int]]:
    await get_entity(session, Campaign, campaign_id)
    rows = (
        await session.execute(
            select(Account, CampaignAccount)
            .join(CampaignAccount, CampaignAccount.account_id == Account.id)
            .where(CampaignAccount.campaign_id == campaign_id)
            .order_by(CampaignAccount.priority, Account.id)
        )
    ).all()
    if selected is not None:
        accounts = (await session.scalars(select(Account).where(Account.id.in_(selected)))).all()
        if len(accounts) != len(set(selected)):
            raise ConflictError("Selected account not found")
        return [
            (a, a.campaign_send_quota or settings.account_campaign_send_quota, i)
            for i, aid in enumerate(selected)
            for a in accounts
            if a.id == aid
        ]
    if rows:
        return [
            (a, membership.max_submissions, membership.priority)
            for a, membership in rows
            if membership.enabled
        ]
    accounts = (
        await session.scalars(
            select(Account).where(Account.status == AccountStatus.active).order_by(Account.id)
        )
    ).all()
    return [
        (a, a.campaign_send_quota or settings.account_campaign_send_quota, i)
        for i, a in enumerate(accounts)
    ]


async def save_pool(
    session: AsyncSession, campaign_id: UUID, ids: list[UUID], settings: Settings
) -> None:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None:
        await get_entity(session, Campaign, campaign_id)
        raise AssertionError("unreachable")
    if campaign.status.value not in {"draft", "ready"}:
        raise ConflictError("Account pool cannot change after sending starts")
    accounts = await pool(session, campaign_id, settings, ids)
    old = (
        await session.scalars(
            select(CampaignAccount).where(CampaignAccount.campaign_id == campaign_id)
        )
    ).all()
    for old_membership in old:
        old_membership.enabled = False
    for account, quota, order in accounts:
        membership = await session.get(CampaignAccount, (campaign_id, account.id))
        if membership is None:
            membership = CampaignAccount(campaign_id=campaign_id, account_id=account.id)
            session.add(membership)
        membership.enabled, membership.max_submissions, membership.priority = True, quota, order
    await session.flush()
