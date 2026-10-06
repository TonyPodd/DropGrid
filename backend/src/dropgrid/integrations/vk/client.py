from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SubmissionReceipt:
    post_url: str | None = None


class VKIntegrationError(Exception):
    """Sanitized error; never include credentials in its message."""


class VKClient(Protocol):
    """Future official-API boundary. No implementation or HTTP calls in this phase."""

    async def submit(
        self,
        *,
        group_id: int,
        track_owner_id: int,
        track_audio_id: int,
        caption: str | None,
    ) -> SubmissionReceipt: ...
