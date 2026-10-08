"""Explicit local user-token import and read-only capability diagnostics."""

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import Account
from dropgrid.domain.enums import AccountStatus
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import VKError, VKProtocolError
from dropgrid.integrations.vk.token_storage import AccountTokenCipher
from dropgrid.services.catalog import ConflictError, get_entity


async def import_token(
    session: AsyncSession,
    account_id: UUID,
    token: SecretStr,
    client: VKClient,
    cipher: AccountTokenCipher,
) -> Account:
    account = await get_entity(session, Account, account_id)
    if account.status.value == "disabled":
        raise ConflictError("Account is disabled")
    # Validate encryption setup before contacting VK; never persist unvalidated credentials.
    encrypted = cipher.encrypt(token, account_id)
    user = await client.get_current_user(access_token=token, account_id=account_id)
    # Re-read with a lock after network work to prevent concurrent identity replacement.
    await session.refresh(account, with_for_update=True)
    if account.status.value == "disabled":
        raise ConflictError("Account is disabled")
    if account.vk_user_id is not None and account.vk_user_id != user.id:
        raise ConflictError("Token belongs to a different VK user")
    account.vk_user_id = user.id
    if user.display_name:
        account.name = user.display_name[:200]
    account.status = AccountStatus.active
    account.encrypted_access_token = encrypted
    await session.flush()
    return account


async def capabilities(
    session: AsyncSession,
    account_id: UUID,
    community_id: int,
    client: VKClient,
    tokens: TokenProvider,
) -> dict[str, object]:
    account = await get_entity(session, Account, account_id)
    result: dict[str, object] = {
        "token_valid": False,
        "current_user_id": None,
        "community_resolved": False,
        "wall_read": False,
        "is_admin": None,
        "is_member": None,
        "write_capability": "UNTESTED",
        "error": None,
    }
    try:
        token = await tokens.get_token(account_id)
        user = await client.get_current_user(access_token=token, account_id=account_id)
        if user.id != account.vk_user_id:
            raise VKProtocolError("users.get", "Account identity mismatch")
        result.update(token_valid=True, current_user_id=user.id)
        group = await client.resolve_community(
            f"club{community_id}", access_token=token, account_id=account_id
        )
        if group.id != community_id:
            raise VKProtocolError("groups.getById", "Resolved community does not match target")
        result.update(
            community_resolved=True,
            is_admin=None if group.is_admin is None else bool(group.is_admin),
            is_member=None if group.is_member is None else bool(group.is_member),
        )
        await client.get_wall_posts(
            f"club{community_id}", access_token=token, account_id=account_id, count=1
        )
        result["wall_read"] = True
    except VKError as error:
        result["error"] = error.as_dict()
    return result
