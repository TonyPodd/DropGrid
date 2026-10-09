import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dropgrid.integrations.vk.errors import VKInputError


@dataclass(frozen=True)
class VKAttachment:
    type: Literal["photo", "audio"]
    owner_id: int
    media_id: int
    access_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            self.type not in {"photo", "audio"}
            or type(self.owner_id) is not int
            or not 0 < abs(self.owner_id) <= 2**63 - 1
            or type(self.media_id) is not int
            or not 0 < self.media_id <= 2**63 - 1
        ):
            raise VKInputError("attachment", "Invalid attachment identity")
        if self.access_key is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{1,256}", self.access_key
        ):
            raise VKInputError("attachment", "Invalid attachment access key")

    def serialize(self) -> str:
        key = f"_{self.access_key}" if self.access_key else ""
        return f"{self.type}{self.owner_id}_{self.media_id}{key}"


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class VKUser(ResponseModel):
    id: int = Field(gt=0)
    first_name: str = ""
    last_name: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class VKCommunity(ResponseModel):
    id: int = Field(gt=0)
    name: str = ""
    screen_name: str | None = None
    is_closed: int = 0
    deactivated: str | None = None
    is_member: int | None = None
    is_admin: int | None = None


class WallPosts(ResponseModel):
    count: int = Field(ge=0)
    items: list[dict[str, Any]]


class WallPostDetails(ResponseModel):
    id: int = Field(ge=0)
    owner_id: int | None = None
    from_id: int | None = None
    date: int | None = None
    post_type: str | None = None
    is_pinned: int | None = None
    text: str = Field(default="", repr=False)
    attachments: list[dict[str, Any]] = Field(default_factory=list, repr=False)


class WallPostsById(ResponseModel):
    items: list[WallPostDetails]


class VKNotification(ResponseModel):
    # VK adds notification types; do not restrict this to a known enum.
    type: str | None = None
    date: int | None = None
    feedback: dict[str, Any] | None = Field(default=None, repr=False)
    parent: dict[str, Any] | None = Field(default=None, repr=False)
    reply: dict[str, Any] | None = Field(default=None, repr=False)


class VKNotifications(ResponseModel):
    count: int = Field(ge=0)
    items: list[VKNotification] = Field(repr=False)
    next_from: str | None = Field(default=None, repr=False)
    profiles: list[dict[str, Any]] = Field(default_factory=list, repr=False)
    groups: list[dict[str, Any]] = Field(default_factory=list, repr=False)


class WallPostReceipt(ResponseModel):
    # Schema does not promise a positive identifier for every accepted wall.post.
    post_id: int = Field(ge=0)


class WallUploadServer(ResponseModel):
    upload_url: str = Field(repr=False)
    album_id: int
    user_id: int


class WallUploadResult(ResponseModel):
    server: int
    photo: str = Field(min_length=1, repr=False)
    hash: str = Field(min_length=1, repr=False)


class SavedPhoto(ResponseModel):
    id: int = Field(gt=0)
    owner_id: int
    access_key: str | None = Field(default=None, repr=False)

    def attachment(self) -> VKAttachment:
        return VKAttachment("photo", self.owner_id, self.id, self.access_key)


type ResolutionStatus = Literal[
    "resolved",
    "not_found",
    "deactivated",
    "private_or_unavailable",
    "transient_error",
    "unresolved",
]


class CommunityResolution(ResponseModel):
    reference: str
    status: ResolutionStatus
    group: VKCommunity | None = None
    error_code: int | None = None
