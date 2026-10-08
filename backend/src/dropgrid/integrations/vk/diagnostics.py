"""Explicit single-account diagnostics. No worker or grid dispatch path."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Never
from uuid import UUID

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import DevelopmentTokenProvider
from dropgrid.integrations.vk.errors import (
    VKCredentialUnavailableError,
    VKError,
    VKWriteDisabledError,
)
from dropgrid.integrations.vk.helpers import build_suggested_post_request, parse_vk_audio_reference
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.logging import configure_logging


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse ordinarily echoes rejected arguments, which might contain a token.
        self.print_usage()
        self.exit(
            2, "Invalid diagnostic arguments; consult --help. Never pass tokens as arguments.\n"
        )


def parser() -> argparse.ArgumentParser:
    root = SafeArgumentParser(description="DropGrid single-account VK diagnostics")
    commands = root.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    for name in ("account", "community", "wall", "suggest", "suggest-text", "suggest-media"):
        command = commands.add_parser(name)
        command.add_argument(
            "--account-id",
            type=UUID,
            required=name == "suggest-media",
            help="Account with imported DB token"
            if name == "suggest-media"
            else "Must match VK_TEST_ACCOUNT_ID",
        )
        if name in {"community", "wall"}:
            command.add_argument("domain")
        if name == "wall":
            command.add_argument("--suggests", action="store_true")
        if name in {"suggest", "suggest-text", "suggest-media"}:
            command.add_argument("--community-id", type=int, required=True)
        if name in {"suggest", "suggest-media"}:
            command.add_argument("--track", required=True)
            if name == "suggest":
                command.add_argument("--image", type=Path, required=True)
            else:
                command.add_argument("--media-asset-id", type=UUID, required=True)
            command.add_argument("--caption", default="DropGrid integration test")
    return root


async def execute(
    args: argparse.Namespace, settings: Settings, client: VKClient
) -> dict[str, object]:
    if args.command == "suggest-media":
        from dropgrid.db.session import Database
        from dropgrid.integrations.vk.media_diagnostic import require_media_target, suggest_media
        from dropgrid.integrations.vk.token_storage import AccountTokenCipher, DBTokenProvider
        from dropgrid.photos.images import LocalMediaStorage

        require_media_target(client, args.community_id)
        account_id = args.account_id
        if account_id is None:
            raise VKCredentialUnavailableError("credentials", "Explicit Account UUID required")
        db = Database(settings)
        try:
            async with WallPhotoUploader(client) as uploader:
                return await suggest_media(
                    account_id=account_id,
                    community_id=args.community_id,
                    media_asset_id=args.media_asset_id,
                    track=args.track,
                    caption=args.caption,
                    settings=settings,
                    sessions=db.sessions,
                    client=client,
                    tokens=DBTokenProvider(
                        db.sessions, AccountTokenCipher(settings.app_secret_key)
                    ),
                    storage=LocalMediaStorage(settings.media_storage_dir),
                    uploader=uploader,
                )
        finally:
            await db.close()
    # Must refuse writes before token retrieval, file access, limiter or any network request.
    if args.command in {"suggest", "suggest-text"}:
        client.require_diagnostic_target(args.community_id)
        if args.command == "suggest-text" and settings.vk_test_allowed_community_ids != {
            args.community_id
        }:
            raise VKWriteDisabledError("wall.post", "Text diagnostic requires exactly one target")
    account_id = args.account_id or settings.vk_test_account_id
    if account_id is None:
        raise VKCredentialUnavailableError(
            "credentials", "Set VK_TEST_ACCOUNT_ID for the single test account"
        )
    token = await DevelopmentTokenProvider(settings).get_token(account_id)
    user = await client.get_current_user(access_token=token, account_id=account_id)
    if args.command == "account":
        return {"user_id": user.id}
    domain = (
        f"club{args.community_id}" if args.command in {"suggest", "suggest-text"} else args.domain
    )
    community = await client.resolve_community(domain, access_token=token, account_id=account_id)
    if args.command == "community":
        return {"community_id": community.id}
    if args.command == "suggest-text":
        if community.id != args.community_id:
            raise VKCredentialUnavailableError(
                "wall.post", "Resolved community does not match target"
            )
        if community.is_admin:
            raise VKWriteDisabledError("wall.post", "Text diagnostic requires a non-admin account")
        await client.call(
            "wall.get",
            access_token=token,
            account_id=account_id,
            params={"owner_id": -community.id, "count": 1, "filter": "all"},
        )
        receipt = await client.create_wall_post(
            {"owner_id": -community.id, "from_group": 0, "message": "DropGrid integration test"},
            access_token=token,
            account_id=account_id,
        )
        return {
            "post_id": receipt.post_id,
            "owner_id": -community.id,
            "placement": "unverified; inspect wall and suggestions separately",
        }
    posts = await client.get_wall_posts(
        domain,
        access_token=token,
        account_id=account_id,
        count=1 if args.command == "suggest" else 20,
        suggests=args.command == "wall" and args.suggests,
    )
    if args.command == "wall":
        return {"community_id": community.id, "count": posts.count, "returned": len(posts.items)}
    if community.id != args.community_id:
        raise VKCredentialUnavailableError(
            "wall.post", "Resolved community does not match explicit target"
        )
    audio = parse_vk_audio_reference(args.track)
    async with WallPhotoUploader(client) as uploader:
        photo = await uploader.upload(
            args.image, community_id=community.id, account_id=account_id, access_token=token
        )
    payload = build_suggested_post_request(
        community.id, caption=args.caption, photo=photo, audio=audio
    )
    receipt = await client.create_wall_post(payload, access_token=token, account_id=account_id)
    return {
        "post_id": receipt.post_id,
        "owner_id": -community.id,
        "placement": "unverified; community permissions determine suggestion vs publication",
    }


async def main() -> int:
    configure_logging()
    args = parser().parse_args()
    try:
        settings = Settings()
        async with VKClient(settings) as client:
            result = await execute(args, settings, client)
    except VKError as error:
        print(json.dumps({"error": error.as_dict(), "kind": type(error).__name__}))
        return 1
    except Exception:
        # Config/transport/library exceptions may include environment input.
        print(json.dumps({"error": "Diagnostic failed; verify configuration and local inputs"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
