from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from dropgrid.domain.enums import AccountStatus, CampaignStatus, GenderTag, SubmissionStatus
from dropgrid.domain.grid_parser import ParseGridResult
from dropgrid.integrations.vk.errors import VKInputError
from dropgrid.integrations.vk.helpers import parse_vk_audio_reference

Name = Annotated[str, Field(min_length=1, max_length=200, strict=True)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountCreate(Input):
    name: Name
    vk_user_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    gender_tag: GenderTag | None = None
    status: AccountStatus = AccountStatus.active


class AccountPatch(Input):
    name: Name | None = None
    vk_user_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    gender_tag: GenderTag | None = None
    status: AccountStatus | None = None

    @model_validator(mode="after")
    def check_nonnullable(self) -> "AccountPatch":
        for field in ("name", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AccountRead(Output):
    id: UUID
    name: str
    vk_user_id: int | None
    gender_tag: GenderTag | None
    status: AccountStatus
    token_configured: bool
    created_at: datetime
    updated_at: datetime


class AccountTokenInput(Input):
    access_token: SecretStr

    @field_validator("access_token", mode="before")
    @classmethod
    def bounded_token(cls, value: object) -> object:
        if not isinstance(value, str) or not 1 <= len(value) <= 8192:
            raise ValueError("Expected a bounded access token")
        if any(c.isspace() or c == "\0" for c in value):
            raise ValueError("Access token must not contain whitespace")
        return value


class AccountTokenRead(BaseModel):
    account_id: UUID
    vk_user_id: int
    name: str
    valid: bool = True


class CapabilityInput(Input):
    community_id: int = Field(gt=0, le=2**63 - 1)


class CapabilityRead(BaseModel):
    token_valid: bool
    current_user_id: int | None
    community_resolved: bool
    wall_read: bool
    is_admin: bool | None
    is_member: bool | None
    write_capability: str
    error: dict[str, object] | None


class CommunityRead(Output):
    id: UUID
    domain: str
    vk_group_id: int | None
    name: str | None
    category: str | None
    required_gender_tag: GenderTag | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class GridCreate(Input):
    name: Name


class GridRead(Output):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class GridDetail(GridRead):
    communities: list["GridCommunityRead"]
    community_count: int
    categories: list["CategoryCount"]


class GridText(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    text: str = Field(max_length=200_000)


class GridImport(GridText):
    name: Name

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class GridImportRead(BaseModel):
    grid: GridRead
    preview: ParseGridResult


class CampaignCreate(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: Name
    grid_id: UUID
    track_url: str = Field(min_length=1, max_length=2048)
    track_owner_id: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    track_audio_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    caption: str | None = Field(default=None, max_length=10_000)
    publication_check_hours: int = Field(default=72, gt=0, le=2**31 - 1)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("track_url")
    @classmethod
    def vk_track_url(cls, value: str) -> str:
        try:
            parse_vk_audio_reference(value)
        except VKInputError:
            raise ValueError(
                "Expected a VK audio link, for example https://vk.ru/audio1_2"
            ) from None
        return value


class CampaignPatch(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: Name | None = None
    track_url: str | None = Field(default=None, min_length=1, max_length=2048)
    track_owner_id: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    track_audio_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    caption: str | None = Field(default=None, max_length=10_000)
    publication_check_hours: int | None = Field(default=None, gt=0, le=2**31 - 1)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("track_url")
    @classmethod
    def vk_track_url(cls, value: str | None) -> str | None:
        return CampaignCreate.vk_track_url(value) if value is not None else None

    @model_validator(mode="after")
    def check_nonnullable(self) -> "CampaignPatch":
        for field in ("name", "track_url", "publication_check_hours"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class CampaignRead(Output):
    id: UUID
    name: str
    grid_id: UUID
    track_url: str
    track_owner_id: int | None
    track_audio_id: int | None
    caption: str | None
    status: CampaignStatus
    publication_check_hours: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class PrepareRead(BaseModel):
    campaign_id: UUID
    created: int
    total: int


class CommunityResolveInput(Input):
    account_id: UUID


class AccountValidationRead(BaseModel):
    account: AccountRead
    valid: bool
    error: dict[str, object] | None = None


class CategoryCount(BaseModel):
    category: str | None
    count: int


class GridSummary(GridRead):
    community_count: int
    category_count: int


class GridCommunityRead(CommunityRead):
    comment: str | None = None
    content_hint: str | None = None


class GridCommunityPatch(Input):
    comment: str | None = Field(default=None, max_length=3000)
    content_hint: str | None = Field(default=None, max_length=500)


class GridCommunityPage(BaseModel):
    items: list[GridCommunityRead]
    total: int
    page: int
    page_size: int


class CampaignSummary(CampaignRead):
    grid_name: str
    community_count: int
    submission_count: int


class CampaignStats(BaseModel):
    total: int
    statuses: dict[SubmissionStatus, int]
    media_assigned: int = 0
    media_unique: int = 0


class SubmissionRead(BaseModel):
    id: UUID
    community: CommunityRead
    category: str | None
    account_id: UUID | None
    account_name: str | None
    media_asset_id: UUID | None
    media_label: str | None
    status: SubmissionStatus
    attempt_count: int
    error_code: str | None
    error_message: str | None
    published_post_url: str | None
    published_at: datetime | None = None


class PublicationCheckRead(BaseModel):
    submission_id: UUID
    previous_status: SubmissionStatus
    current_status: SubmissionStatus
    suggestion_state: str
    notification_match: bool
    published_post_id: int | None
    published_post_url: str | None
    last_checked_at: datetime | None
    evidence: dict[str, object]


class PublishedResult(BaseModel):
    community: CommunityRead
    category: str | None
    published_post_url: str
    published_at: datetime


class SubmissionPage(BaseModel):
    items: list[SubmissionRead]
    total: int
    page: int
    page_size: int


class DashboardRead(BaseModel):
    counts: dict[str, int]
    campaign_statuses: dict[CampaignStatus, int]
    recent_campaigns: list[CampaignSummary]


class TrackInput(Input):
    track_url: str = Field(min_length=1, max_length=2048)


class TrackRead(BaseModel):
    owner_id: int
    audio_id: int
