"""Local authenticated credentials. Acquisition is external; no plaintext fallback."""

from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import Account
from dropgrid.domain.enums import AccountStatus
from dropgrid.integrations.vk.errors import VKCredentialUnavailableError


class AccountTokenCipher:
    prefix = "fernet:v1:"

    def __init__(self, key: SecretStr) -> None:
        self.key = key

    def _fernet(self) -> Fernet:
        try:
            return Fernet(self.key.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeError):
            raise VKCredentialUnavailableError(
                "credentials", "APP_SECRET_KEY must be a Fernet key; token storage unavailable"
            ) from None

    def encrypt(self, token: SecretStr, account_id: UUID) -> str:
        value = token.get_secret_value()
        if not value or len(value) > 8192 or any(c.isspace() or c == "\0" for c in value):
            raise VKCredentialUnavailableError("credentials", "Invalid token input")
        # Authenticated account binding rejects copied ciphertext from another Account.
        payload = f"{account_id}\0{value}".encode()
        return self.prefix + self._fernet().encrypt(payload).decode("ascii")

    def decrypt(self, value: str, account_id: UUID) -> SecretStr:
        cipher = self._fernet()
        try:
            if not value.startswith(self.prefix):
                raise ValueError
            decoded = cipher.decrypt(value[len(self.prefix) :].encode("ascii")).decode()
            owner, separator, token = decoded.partition("\0")
            if owner != str(account_id) or not separator or not token:
                raise ValueError
            return SecretStr(token)
        except (InvalidToken, ValueError, UnicodeError):
            raise VKCredentialUnavailableError(
                "credentials", "Stored credential cannot be decrypted; import it again"
            ) from None


class DBTokenProvider:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], cipher: AccountTokenCipher
    ) -> None:
        self.sessions, self.cipher = sessions, cipher

    async def get_token(self, account_id: UUID) -> SecretStr:
        async with self.sessions() as session:
            account = await session.get(Account, account_id)
            if (
                account is None
                or account.status != AccountStatus.active
                or account.vk_user_id is None
                or not account.encrypted_access_token
            ):
                raise VKCredentialUnavailableError(
                    "credentials", "Active account with an imported token is required"
                )
            return self.cipher.decrypt(account.encrypted_access_token, account_id)
