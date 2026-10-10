from datetime import datetime
from uuid import UUID

from pydantic import Field

from dropgrid.api.schemas import Input, Output


class MediaPlanInput(Input):
    max_reuse_per_asset: int | None = Field(default=None, ge=1, le=20)
    force: bool = False


class CategoryPlanRead(Output):
    name: str
    submission_count: int
    assigned_count: int = 0
    unique_asset_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class MediaPlanRead(Output):
    near_duplicate_exclusions: int = 0
    source_contributions: dict[str, dict[str, int]] = Field(default_factory=dict)
    campaign_id: UUID
    total_submissions: int
    previously_assigned: int
    newly_assigned: int = 0
    unassigned: int = 0
    unique_assets: int = 0
    downloaded_assets: int = 0
    reused_existing_assets: int = 0
    provider_requests: int = 0
    provider_cache_hits: int = 0
    categories: list[CategoryPlanRead] = Field(default_factory=list)


class MediaAssetRead(Output):
    id: UUID
    category: str | None
    provider: str | None
    provider_asset_id: str | None
    source_url: str | None
    creator_name: str | None
    creator_url: str | None
    license_code: str | None
    license_name: str | None
    license_url: str | None
    attribution_text: str | None
    requires_publication_attribution: bool
    width: int | None
    height: int | None
    mime_type: str | None
    byte_size: int | None
    tags: list[str]
    usage_count: int
    last_used_at: datetime | None
    enabled: bool
    created_at: datetime


class MediaAssetPage(Output):
    items: list[MediaAssetRead]
    total: int
    page: int
    page_size: int
