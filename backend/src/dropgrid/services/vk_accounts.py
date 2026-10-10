from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import Account, Community, utcnow
from dropgrid.domain.enums import AccountStatus
from dropgrid.domain.grid_parser import normalize_vk_community_reference
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import VKAuthenticationError, VKProtocolError
from dropgrid.services.catalog import ConflictError, get_entity


async def enabled_account(session: AsyncSession, account_id: UUID) -> Account:
    account = await get_entity(session, Account, account_id)
    if account.status == AccountStatus.disabled:
        raise ConflictError("Account is disabled")
    return account


async def validate_account(
    session: AsyncSession, account_id: UUID, client: VKClient, tokens: TokenProvider
) -> tuple[Account, VKAuthenticationError | None]:
    account = await enabled_account(session, account_id)
    token = await tokens.get_token(account_id)
    failure: VKAuthenticationError | None = None
    try:
        user = await client.get_current_user(access_token=token, account_id=account_id)
    except VKAuthenticationError as error:
        failure = error
        account.status = AccountStatus.invalid
    else:
        if account.vk_user_id is not None and account.vk_user_id != user.id:
            raise ConflictError("Token belongs to a different VK user")
        account.vk_user_id = user.id
        if user.display_name:
            account.name = user.display_name[:200]
        account.status = AccountStatus.active
    account.last_validated_at = utcnow()
    await session.flush()
    # Return authentication failure rather than raise: the invalid state must commit.
    return account, failure


async def resolve_community(
    session: AsyncSession,
    community_id: UUID,
    account_id: UUID,
    client: VKClient,
    tokens: TokenProvider,
) -> Community:
    community = await get_entity(session, Community, community_id)
    await enabled_account(session, account_id)
    token = await tokens.get_token(account_id)
    group = await client.resolve_community(
        community.domain, access_token=token, account_id=account_id
    )
    canonical = community.domain
    error: VKProtocolError | None = None
    if group.screen_name:
        try:
            canonical = normalize_vk_community_reference(group.screen_name)
        except ValueError:
            error = VKProtocolError("groups.getById", "Invalid canonical VK community domain")
    if error:
        raise error
    existing = await session.scalar(
        select(Community.id).where(Community.domain == canonical, Community.id != community_id)
    )
    if existing is not None:
        raise ConflictError("Canonical community domain already belongs to another record")
    community.resolution_status = (
        "private_or_unavailable"
        if group.is_closed and not group.is_member and not group.is_admin
        else "resolved"
    )
    community.resolution_checked_at = utcnow()
    community.resolution_error_code = None
    community.vk_group_id = group.id
    community.domain = canonical
    if group.name:
        community.name = group.name[:200]
    await session.flush()
    return community
