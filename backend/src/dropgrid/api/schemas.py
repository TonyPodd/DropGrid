from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dropgrid.domain.enums import AccountStatus, CampaignStatus, GenderTag

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
    created_at: datetime
    updated_at: datetime


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
    communities: list[CommunityRead]


class GridText(Input):
    text: str = Field(max_length=200_000)


class GridImport(GridText):
    name: Name


class CampaignCreate(Input):
    name: Name
    grid_id: UUID
    track_url: str = Field(min_length=1, max_length=2048)
    track_owner_id: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    track_audio_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    caption: str | None = Field(default=None, max_length=10_000)
    publication_check_hours: int = Field(default=72, gt=0, le=2**31 - 1)

    @field_validator("track_url")
    @classmethod
    def vk_track_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() not in {"vk.com", "vk.ru", "www.vk.com", "www.vk.ru"}
            or not parsed.path.strip("/")
        ):
            raise ValueError("Expected an HTTP(S) VK track URL")
        return value


class CampaignPatch(Input):
    name: Name | None = None
    track_url: str | None = Field(default=None, min_length=1, max_length=2048)
    track_owner_id: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    track_audio_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    caption: str | None = Field(default=None, max_length=10_000)
    publication_check_hours: int | None = Field(default=None, gt=0, le=2**31 - 1)

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
