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
from dropgrid.integrations.vk.errors import VKCredentialUnavailableError, VKError
from dropgrid.integrations.vk.helpers import build_suggested_post_request, parse_vk_audio_reference
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.logging import configure_logging


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse ordinarily echoes rejected arguments, which might contain a token.
        self.print_usage()
        self.exit(2, "Invalid diagnostic arguments; consult --help. Tokens must use environment.\n")


def parser() -> argparse.ArgumentParser:
    root = SafeArgumentParser(description="DropGrid single-account VK diagnostics")
    commands = root.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    for name in ("account", "community", "wall", "suggest"):
        command = commands.add_parser(name)
        command.add_argument("--account-id", type=UUID, help="Must match VK_TEST_ACCOUNT_ID")
        if name in {"community", "wall"}:
            command.add_argument("domain")
        if name == "wall":
            command.add_argument("--suggests", action="store_true")
        if name == "suggest":
            command.add_argument("--community-id", type=int, required=True)
            command.add_argument("--track", required=True)
            command.add_argument("--image", type=Path, required=True)
            command.add_argument("--caption", default="DropGrid integration test")
    return root


async def execute(
    args: argparse.Namespace, settings: Settings, client: VKClient
) -> dict[str, object]:
    # Must refuse writes before token retrieval, file access, limiter or any network request.
    if args.command == "suggest":
        client.require_diagnostic_target(args.community_id)
    account_id = args.account_id or settings.vk_test_account_id
    if account_id is None:
        raise VKCredentialUnavailableError(
            "credentials", "Set VK_TEST_ACCOUNT_ID for the single test account"
        )
    token = await DevelopmentTokenProvider(settings).get_token(account_id)
    user = await client.get_current_user(access_token=token, account_id=account_id)
    if args.command == "account":
        return {"user_id": user.id}
    domain = f"club{args.community_id}" if args.command == "suggest" else args.domain
    community = await client.resolve_community(domain, access_token=token, account_id=account_id)
    if args.command == "community":
        return {"community_id": community.id}
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
