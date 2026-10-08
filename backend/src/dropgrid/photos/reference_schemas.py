from datetime import datetime
from uuid import UUID

from pydantic import Field

from dropgrid.api.schemas import Input, Output


class ProfileInput(Input):
    desired_content: str | None = Field(default=None, max_length=3000)
    avoid_content: str | None = Field(default=None, max_length=3000)
    style_notes: str | None = Field(default=None, max_length=3000)
    reference_target_count: int = Field(default=100, gt=0, le=300)
    archive_reuse_enabled: bool = False
    archive_reuse_min_age_days: int = Field(default=180, ge=0, le=36500)


class ProfileRead(ProfileInput, Output):
    community_id: UUID
    references_last_synced_at: datetime | None = None
    reference_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReferenceSyncInput(Input):
    account_id: UUID | None = None
    target_count: int | None = Field(default=None, ge=1, le=300)


class ReferenceSyncRead(Output):
    posts_scanned: int = 0
    photo_posts_found: int = 0
    references_created: int = 0
    references_existing: int = 0
    references_embedded: int = 0
    downloads_succeeded: int = 0
    warnings: list[str] = Field(default_factory=list)


class ReferenceRead(Output):
    id: UUID
    community_id: UUID
    vk_post_id: int
    vk_photo_owner_id: int
    vk_photo_id: int
    posted_at: datetime
    embedding_model: str | None
    reuse_eligible: bool


class ReferencePage(Output):
    items: list[ReferenceRead]
    total: int
    page: int
    page_size: int


class PhotoPreviewInput(Input):
    grid_id: UUID | None = None
    candidate_limit: int = Field(default=8, ge=1, le=12)


class PhotoPreviewItem(Output):
    media_asset_id: UUID
    base_score: float
    visual_score: float | None
    final_score: float
    best_similarity: float | None
    reference_count: int


class PhotoPreviewRead(Output):
    community_id: UUID
    category: str | None
    references: list[UUID]
    category_only: list[PhotoPreviewItem]
    community_aware: list[PhotoPreviewItem]
    warnings: list[str] = Field(default_factory=list)
