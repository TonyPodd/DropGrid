from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from dropgrid.api.schemas import Input, Output


class ProfileInput(Input):
    desired_content: str | None = Field(default=None, max_length=3000)
    avoid_content: str | None = Field(default=None, max_length=3000)
    style_notes: str | None = Field(default=None, max_length=3000)
    reference_target_count: int = Field(default=100, gt=0, le=300)
    archive_reuse_enabled: bool = False
    archive_reuse_min_age_days: int = Field(default=180, ge=0, lt=3650)
    archive_reuse_max_age_days: int = Field(default=540, gt=0, le=3650)

    @model_validator(mode="after")
    def valid_window(self) -> "ProfileInput":
        if self.archive_reuse_max_age_days <= self.archive_reuse_min_age_days:
            raise ValueError("Archive max age must exceed min age")
        return self


class ProfileRead(ProfileInput, Output):
    community_id: UUID
    references_last_synced_at: datetime | None = None
    reference_count: int = 0
    archive_discovered_count: int = 0
    archive_eligible_count: int = 0
    archive_oldest_eligible_at: datetime | None = None
    archive_newest_eligible_at: datetime | None = None
    archive_last_synced_at: datetime | None = None
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
    embeddings_available: int = 0
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
    reference_role: str = "core"
    reference_density: float | None = None
    reference_nearest_similarity: float | None = None
    reference_duplicate: bool = False
    reuse_eligible: bool


class ReferencePage(Output):
    items: list[ReferenceRead]
    total: int
    page: int
    page_size: int


class PhotoPreviewInput(Input):
    grid_id: UUID | None = None
    candidate_limit: int = Field(default=8, ge=1, le=12)
    include_archive: bool = True
    diagnostics: bool = False


class VisualEngineRead(Output):
    enabled: bool = False
    model: str | None = None
    compatible_reference_count: int = 0
    candidate_embeddings_available: int = 0
    active: bool = False
    reason_if_inactive: str | None = None


class ReferenceMatch(Output):
    reference_id: UUID
    similarity: float


class PhotoPreviewItem(Output):
    preview_id: UUID | None = None
    publication_eligible: bool = True
    pin_url: str | None = None
    title: str = ""
    top_references: list[ReferenceMatch] = Field(default_factory=list)
    provider: str = ""
    media_asset_id: UUID | None = None
    reference_id: UUID | None = None
    source: str = "library"
    source_identity: str = ""
    retrieval_queries: list[str] = Field(default_factory=list)
    original_posted_at: datetime | None = None
    age_days: float | None = None
    age_reuse_score: float = 0
    base_score: float
    visual_score: float | None
    final_score: float
    best_similarity: float | None
    reference_count: int


class ArchiveAgeStratum(Output):
    min_age_days: float
    max_age_days: float
    candidate_count: int


class ArchiveShortlistItem(Output):
    source_identity: str
    age_days: float


class PhotoPreviewRead(Output):
    pinterest_status: str = "disabled"
    pinterest_queries: list[str] = Field(default_factory=list)
    pinterest_retrieved: int = 0
    pinterest_embedded: int = 0
    pinterest: list[PhotoPreviewItem] = Field(default_factory=list)
    community_ranked_pinterest: list[PhotoPreviewItem] = Field(default_factory=list)
    visual_engine: VisualEngineRead = Field(default_factory=VisualEngineRead)
    timings_ms: dict[str, float] | None = None
    community_id: UUID
    category: str | None
    comment: str | None = None
    content_hint: str | None = None
    desired_content: str | None = None
    avoid_content: str | None = None
    generated_queries: list[str] = Field(default_factory=list)
    references: list[UUID]
    category_only: list[PhotoPreviewItem]
    community_aware: list[PhotoPreviewItem]
    archive_age_strata: list[ArchiveAgeStratum] = Field(default_factory=list)
    archive_shortlist: list[ArchiveShortlistItem] = Field(default_factory=list)
    mixed_source: list[PhotoPreviewItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ArchiveSyncInput(Input):
    account_id: UUID | None = None
    max_pages: int = Field(default=20, ge=1, le=50)


class ArchiveSyncRead(Output):
    seek_calls: int = 0
    seek_posts_inspected: int = 0
    start_offset: int = 0
    end_offset: int = 0
    scan_bound_reached: bool = False
    posts_scanned: int = 0
    pages_read: int = 0
    candidates_discovered: int = 0
    candidates_existing: int = 0
    crossed_max_age: bool = False
    exhausted: bool = False
    warnings: list[str] = Field(default_factory=list)
