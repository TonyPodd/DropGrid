"""Deterministic campaign capacity planning. No network calls or write retries."""

from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.config import Settings
from dropgrid.db.models import Account, Campaign, CampaignAccount, Community, Submission
from dropgrid.domain.enums import AccountStatus, GenderTag
from dropgrid.services.catalog import ConflictError, get_entity


def compatible(community: Community, account: Account) -> bool:
    return community.required_gender_tag in {None, GenderTag.unspecified, account.gender_tag}


def distribute(
    rows: list[tuple[Submission, Community]], accounts: list[tuple[Account, int, int]]
) -> tuple[dict[UUID, UUID], dict[UUID, str]]:
    """Allocate constrained genders first, then balance by assigned count/priority/UUID."""
    assigned: dict[UUID, UUID] = {}
    reasons: dict[UUID, str] = {}
    counts: Counter[UUID] = Counter()
    usable = [
        (a, quota, order)
        for a, quota, order in accounts
        if a.status == AccountStatus.active and a.vk_user_id and a.encrypted_access_token
    ]
    options = {
        row.id: [(a, quota, order) for a, quota, order in usable if compatible(c, a)]
        for row, c in rows
    }
    for row, _community in sorted(
        rows, key=lambda r: (len(options[r[0].id]), r[1].domain, str(r[0].id))
    ):
        possible = options[row.id]
        available = [(a, quota, order) for a, quota, order in possible if counts[a.id] < quota]
        if not available:
            reasons[row.id] = (
                "account_capacity_exhausted" if possible else "account_gender_mismatch"
            )
            continue
        account, _, _ = min(
            available, key=lambda item: (counts[item[0].id], item[2], str(item[0].id))
        )
        assigned[row.id] = account.id
        counts[account.id] += 1
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
