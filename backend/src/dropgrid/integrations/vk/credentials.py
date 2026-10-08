from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from dropgrid.config import Settings
from dropgrid.integrations.vk.errors import VKCredentialUnavailableError


class TokenProvider(Protocol):
    """Credential boundary: external acquisition, encrypted DB or explicit dev binding."""

    async def get_token(self, account_id: UUID) -> SecretStr: ...


class DevelopmentTokenProvider:
    """An explicit single-account env binding; never interprets encrypted DB data as plaintext."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def get_token(self, account_id: UUID) -> SecretStr:
        if (
            self.settings.app_env != "development"
            or account_id != self.settings.vk_test_account_id
            or self.settings.vk_test_access_token is None
            or not self.settings.vk_test_access_token.get_secret_value().strip()
        ):
            raise VKCredentialUnavailableError(
                "credentials", "No secure token provider configured for this account"
            )
        return self.settings.vk_test_access_token
