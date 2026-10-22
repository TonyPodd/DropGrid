"""One explicit stale-suggestion cleanup check for one account and one community.

`--dry-run` only reads this account's suggestions there. A real run needs the VK
write flag and the community in VK_TEST_ALLOWED_COMMUNITY_IDS, exactly like the
other write diagnostics, and deletes with the same rule the sender uses.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import Account, Community, utcnow
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import VKCredentialUnavailableError, VKError
from dropgrid.services.catalog import get_entity


async def suggestion_cleanup(
    *,
    account_id: UUID,
    community_id: int,
    max_age_days: int,
    dry_run: bool,
    sessions: async_sessionmaker[AsyncSession],
    client: VKClient,
    tokens: TokenProvider,
) -> dict[str, object]:
    from sqlalchemy import select

    from dropgrid.services.sending import record_withdrawn, stale_suggestions

    # Writes are refused before any token retrieval or network request.
    if not dry_run:
        client.require_diagnostic_target(community_id)
        client.require_write("wall.delete")
    async with sessions() as session:
        account = await get_entity(session, Account, account_id)
        local = await session.scalar(select(Community).where(Community.vk_group_id == community_id))
        author, local_id = account.vk_user_id, local.id if local else None
    if author is None:
        raise VKCredentialUnavailableError("credentials", "Account has no VK identity")
    token = await tokens.get_token(account_id)
    posts = await client.get_own_suggestions(
        community_id, access_token=token, account_id=account_id
    )
    cutoff = utcnow() - timedelta(days=max_age_days)
    stale = stale_suggestions(posts, community_id, author, cutoff)
    deleted: list[int] = []
    errors: list[dict[str, object]] = []
    for post in [] if dry_run else stale:
        try:
            await client.delete_wall_post(
                -community_id, post.id, access_token=token, account_id=account_id
            )
        except VKError as error:
            errors.append({"post_id": post.id, "error_code": error.code})
            continue
        deleted.append(post.id)
        if local_id is not None:
            await record_withdrawn(sessions, local_id, account_id, post.id)
    return {
        "community_id": community_id,
        "listed": len(posts),
        "stale": [post.id for post in stale],
        "deleted": deleted,
        "errors": errors,
        "dry_run": dry_run,
    }
