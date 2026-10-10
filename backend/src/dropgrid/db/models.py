from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dropgrid.domain.enums import AccountStatus, CampaignStatus, GenderTag, SubmissionStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Updated:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Account(Identity, Updated, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("vk_user_id", name="uq_accounts_vk_user_id"),
        CheckConstraint(
            "campaign_send_quota IS NULL OR campaign_send_quota > 0", name="quota_positive"
        ),
    )
    vk_user_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campaign_send_quota: Mapped[int | None] = mapped_column(Integer)
    gender_tag: Mapped[GenderTag | None] = mapped_column(Enum(GenderTag, name="gender_tag"))
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status"), default=AccountStatus.active
    )
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    vk_next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def token_configured(self) -> bool:
        return bool(self.encrypted_access_token)


class Community(Identity, Updated, Base):
    __tablename__ = "communities"
    vk_group_id: Mapped[int | None] = mapped_column(BigInteger)
    domain: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    required_gender_tag: Mapped[GenderTag | None] = mapped_column(
        Enum(GenderTag, name="gender_tag")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolution_status: Mapped[str] = mapped_column(
        String(32), default="unresolved", server_default="unresolved"
    )
    resolution_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_error_code: Mapped[int | None] = mapped_column(Integer)


class Grid(Identity, Updated, Base):
    __tablename__ = "grids"
    name: Mapped[str] = mapped_column(String(200))


class GridCommunity(Base):
    __tablename__ = "grid_communities"
    grid_id: Mapped[UUID] = mapped_column(ForeignKey("grids.id"), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"), primary_key=True)
    # Keep the import category per grid without overwriting another grid's category.
    category: Mapped[str | None] = mapped_column(String(200))
    source_reference: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    content_hint: Mapped[str | None] = mapped_column(Text)


class MediaAsset(Identity, Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("usage_count >= 0", name="usage_count_nonnegative"),
        UniqueConstraint("provider", "provider_asset_id", name="uq_media_provider_asset"),
        UniqueConstraint("sha256", name="uq_media_sha256"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        CheckConstraint("byte_size IS NULL OR byte_size > 0", name="byte_size_positive"),
    )
    storage_key: Mapped[str] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    provider: Mapped[str | None] = mapped_column(String(50), index=True)
    provider_asset_id: Mapped[str | None] = mapped_column(String(100))
    creator_name: Mapped[str | None] = mapped_column(String(200))
    creator_url: Mapped[str | None] = mapped_column(Text)
    license_code: Mapped[str | None] = mapped_column(String(100))
    license_name: Mapped[str | None] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(Text)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    requires_publication_attribution: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(16), index=True)
    visual_embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    visual_embedding_model: Mapped[str | None] = mapped_column(String(200))
    visual_embedding_dimensions: Mapped[int | None] = mapped_column(Integer)


class CommunityContentProfile(Updated, Base):
    __tablename__ = "community_content_profiles"
    __table_args__ = (
        CheckConstraint(
            "reference_target_count > 0 AND reference_target_count <= 300", name="target_bound"
        ),
        CheckConstraint("archive_reuse_min_age_days >= 0", name="reuse_age_nonnegative"),
        CheckConstraint(
            "archive_reuse_max_age_days > archive_reuse_min_age_days "
            "AND archive_reuse_max_age_days <= 3650",
            name="reuse_window",
        ),
    )
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"), primary_key=True)
    desired_content: Mapped[str | None] = mapped_column(Text)
    avoid_content: Mapped[str | None] = mapped_column(Text)
    style_notes: Mapped[str | None] = mapped_column(Text)
    reference_target_count: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    archive_reuse_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    archive_reuse_min_age_days: Mapped[int] = mapped_column(
        Integer, default=180, server_default="180"
    )
    archive_reuse_max_age_days: Mapped[int] = mapped_column(
        Integer, default=540, server_default="540"
    )
    archive_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archive_lease_token: Mapped[UUID | None] = mapped_column()
    archive_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preview_lease_token: Mapped[UUID | None]
    preview_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    references_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sync_lease_token: Mapped[UUID | None] = mapped_column()
    sync_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommunityReferencePhoto(Identity, Base):
    __tablename__ = "community_reference_photos"
    __table_args__ = (
        UniqueConstraint(
            "community_id", "vk_photo_owner_id", "vk_photo_id", name="uq_community_reference_photo"
        ),
        Index("ix_community_reference_photos_posted_at", "community_id", "posted_at"),
    )
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"), index=True)
    vk_post_id: Mapped[int] = mapped_column(BigInteger)
    vk_photo_owner_id: Mapped[int] = mapped_column(BigInteger)
    vk_photo_id: Mapped[int] = mapped_column(BigInteger)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(CHAR(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(16))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    is_style_reference: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    archive_discovered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    reuse_eligible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    reference_role: Mapped[str] = mapped_column(String(12), default="core", server_default="core")
    reference_cluster_size: Mapped[int | None] = mapped_column(Integer)
    reference_role_reason: Mapped[str] = mapped_column(
        String(40), default="density", server_default="density"
    )
    reference_density: Mapped[float | None] = mapped_column(Float)
    reference_nearest_similarity: Mapped[float | None] = mapped_column(Float)
    reference_duplicate: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class PhotoSearchCache(Base):
    __tablename__ = "photo_search_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidates: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaProviderImport(Base):
    __tablename__ = "media_provider_imports"
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    provider_asset_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    media_asset_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    creator_name: Mapped[str] = mapped_column(String(200))
    creator_url: Mapped[str | None] = mapped_column(Text)
    license_code: Mapped[str] = mapped_column(String(100))
    source_community_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("communities.id"), index=True
    )
    source_post_id: Mapped[int | None] = mapped_column(BigInteger)
    source_photo_owner_id: Mapped[int | None] = mapped_column(BigInteger)
    source_photo_id: Mapped[int | None] = mapped_column(BigInteger)
    source_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    source_perceptual_hash: Mapped[str | None] = mapped_column(String(16))
    source_embedding: Mapped[str | None] = mapped_column(Text)
    source_embedding_model: Mapped[str | None] = mapped_column(String(100))
    source_embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    image_source_url: Mapped[str | None] = mapped_column(Text)
    retrieval_query: Mapped[str | None] = mapped_column(String(100))


class CommunityPhotoFeedback(Base):
    __tablename__ = "community_photo_feedback"
    __table_args__ = (CheckConstraint("rating IN ('like', 'dislike')", name="feedback_rating"),)
    community_id: Mapped[UUID] = mapped_column(
        ForeignKey("communities.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_identity: Mapped[str] = mapped_column(String(100), primary_key=True)
    rating: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommunityMediaUsage(Identity, Base):
    __tablename__ = "community_media_usage"
    __table_args__ = (
        UniqueConstraint(
            "community_id",
            "source_provider",
            "source_identity",
            name="uq_community_media_usage_source",
        ),
        CheckConstraint("use_count > 0", name="use_count_positive"),
        Index("ix_community_media_usage_recent", "community_id", "last_used_at"),
    )
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"))
    media_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    source_provider: Mapped[str] = mapped_column(String(50))
    source_identity: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str | None] = mapped_column(CHAR(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(16))
    first_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=1)
    last_submission_id: Mapped[UUID | None] = mapped_column(ForeignKey("submissions.id"))


class PhotoProviderState(Base):
    __tablename__ = "photo_provider_state"
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    next_request_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_seconds: Mapped[float] = mapped_column(default=1.0)


class PhotoPlanLease(Base):
    __tablename__ = "photo_plan_leases"
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    token: Mapped[UUID] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Campaign(Identity, Base):
    __tablename__ = "campaigns"
    __table_args__ = (CheckConstraint("publication_check_hours > 0", name="check_hours_positive"),)
    name: Mapped[str] = mapped_column(String(200))
    grid_id: Mapped[UUID] = mapped_column(ForeignKey("grids.id"), index=True)
    track_url: Mapped[str] = mapped_column(Text)
    track_owner_id: Mapped[int | None] = mapped_column(BigInteger)
    track_audio_id: Mapped[int | None] = mapped_column(BigInteger)
    caption: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"), default=CampaignStatus.draft
    )
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"))
    is_dry_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    dry_run_scope: Mapped[list[str] | None] = mapped_column(JSONB)
    photo_review_mode: Mapped[str] = mapped_column(
        String(24), default="AUTO", server_default="AUTO"
    )
    preparation_state: Mapped[str] = mapped_column(
        String(24), default="legacy", server_default="legacy"
    )
    publication_check_hours: Mapped[int] = mapped_column(Integer, default=72)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Submission(Identity, Updated, Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "community_id", name="uq_submission_campaign_community"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index("ix_submissions_monitor_due", "status", "vk_next_check_at"),
        Index("ix_submissions_send_due", "status", "vk_send_next_at"),
        Index("ix_submissions_vk_receipt", "account_id", "vk_suggested_post_id", "community_id"),
    )
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"))
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"))
    media_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"), default=SubmissionStatus.pending
    )
    ranking_model_version: Mapped[str] = mapped_column(
        String(100), default="deterministic-v1", server_default="deterministic-v1"
    )
    photo_source: Mapped[str | None] = mapped_column(String(50))
    photo_attention: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_post_url: Mapped[str | None] = mapped_column(Text)
    vk_suggested_post_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_canonical_photo_owner_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_canonical_photo_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_audio_owner_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_audio_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_published_post_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_publication_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_monitor_lease_token: Mapped[UUID | None] = mapped_column()
    vk_monitor_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_monitor_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    vk_send_phase: Mapped[str | None] = mapped_column(String(32))
    vk_send_guid: Mapped[UUID | None]
    vk_send_lease_token: Mapped[UUID | None]
    vk_send_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_send_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vk_send_receipt_post_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_photo_upload_owner_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_photo_upload_id: Mapped[int | None] = mapped_column(BigInteger)
    vk_send_readback_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class VKNotificationCursor(Base):
    __tablename__ = "vk_notification_cursors"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaPreparationJob(Identity, Updated, Base):
    __tablename__ = "media_preparation_jobs"
    __table_args__ = (
        UniqueConstraint("grid_id", "community_id", "account_id", name="uq_media_prep_context"),
        CheckConstraint(
            "state IN ('queued', 'running', 'ready', 'failed', 'transient')", name="state"
        ),
        Index("ix_media_prep_claim", "state", "lease_until"),
    )
    grid_id: Mapped[UUID] = mapped_column(ForeignKey("grids.id", ondelete="CASCADE"))
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"))
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))


class ReferenceSyncJob(Identity, Updated, Base):
    __tablename__ = "reference_sync_jobs"
    __table_args__ = (
        Index(
            "ix_reference_sync_jobs_active",
            "community_id",
            unique=True,
            postgresql_where=text("state NOT IN ('ready', 'failed')"),
        ),
        Index("ix_reference_sync_jobs_due", "state", "lease_until"),
    )
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"))
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"))
    target_count: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))


class PhotoPreviewCache(Identity, Updated, Base):
    """Preview bytes only; no FK to MediaAsset, Campaign or Submission."""

    __tablename__ = "photo_preview_cache"
    __table_args__ = (UniqueConstraint("provider", "provider_asset_id", name="uq_preview_pin"),)
    provider: Mapped[str] = mapped_column(String(50))
    provider_asset_id: Mapped[str] = mapped_column(String(100))
    candidate: Mapped[dict[str, object]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    perceptual_hash: Mapped[str] = mapped_column(String(16))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)


class PhotoOperationJob(Identity, Updated, Base):
    """One bounded read-only queue for archive and preview operations."""

    __tablename__ = "photo_operation_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('archive', 'preview')", name="kind"),
        CheckConstraint("state IN ('queued', 'running', 'ready', 'failed')", name="state"),
        Index(
            "ix_photo_operation_active",
            "community_id",
            unique=True,
            postgresql_where=text("state NOT IN ('ready', 'failed')"),
        ),
        Index("ix_photo_operation_due", "state", "lease_until"),
    )
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    current: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total: Mapped[int | None] = mapped_column(Integer)
    counters: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default="{}")
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignAccount(Base):
    __tablename__ = "campaign_accounts"
    __table_args__ = (CheckConstraint("max_submissions > 0", name="quota_positive"),)
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    max_submissions: Mapped[int] = mapped_column(Integer, default=100)
    assigned_count: Mapped[int] = mapped_column(Integer, default=0)


class CampaignPreparationJob(Identity, Updated, Base):
    __tablename__ = "campaign_preparation_jobs"
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), unique=True
    )
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    stage: Mapped[str] = mapped_column(String(40), default="communities")
    completed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    submission_scope: Mapped[list[str] | None] = mapped_column(JSONB)


class PhotoReviewer(Identity, Base):
    __tablename__ = "photo_reviewers"
    display_name: Mapped[str] = mapped_column(String(100), unique=True)


class PhotoReviewBatch(Identity, Updated, Base):
    __tablename__ = "photo_review_batches"
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("photo_reviewers.id"))
    reviewer_id: Mapped[UUID | None] = mapped_column(ForeignKey("photo_reviewers.id"))
    state: Mapped[str] = mapped_column(String(20), default="open")
    target_count: Mapped[int] = mapped_column(Integer)
    current_position: Mapped[int] = mapped_column(Integer, default=0)
    current_filter: Mapped[str] = mapped_column(String(30), default="pending")


class PhotoReviewBatchItem(Base):
    __tablename__ = "photo_review_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "submission_id"),
        CheckConstraint(
            "state IN ('pending','confirmed','skipped','needs_attention')", name="review_state"
        ),
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("photo_review_batches.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[UUID] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    selection_session_id: Mapped[UUID] = mapped_column(ForeignKey("photo_selection_sessions.id"))
    automatic_rank: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    reviewer_id: Mapped[UUID | None] = mapped_column(ForeignKey("photo_reviewers.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PhotoSelectionSession(Identity, Base):
    __tablename__ = "photo_selection_sessions"
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"))
    category: Mapped[str | None] = mapped_column(String(200))
    content_hint: Mapped[str | None] = mapped_column(String(500))
    baseline_rank: Mapped[int] = mapped_column(Integer)
    proposed_rank: Mapped[int] = mapped_column(Integer)
    learned_rank: Mapped[int | None] = mapped_column(Integer)
    chosen_rank: Mapped[int | None] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ranking_model_version: Mapped[str] = mapped_column(String(100), default="deterministic-v1")
    reviewer_id: Mapped[UUID | None] = mapped_column(ForeignKey("photo_reviewers.id"))


class PhotoSelectionCandidate(Base):
    __tablename__ = "photo_selection_candidates"
    __table_args__ = (CheckConstraint("rank >= 1 AND rank <= 12", name="rank_bounded"),)
    selection_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("photo_selection_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    source_identity: Mapped[str] = mapped_column(String(100))
    media_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    preview_id: Mapped[UUID | None] = mapped_column(ForeignKey("photo_preview_cache.id"))
    reference_id: Mapped[UUID | None] = mapped_column(ForeignKey("community_reference_photos.id"))
    features: Mapped[dict[str, object]] = mapped_column(JSONB)
    candidate: Mapped[dict[str, object]] = mapped_column(JSONB)
    displayed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    operator_rating: Mapped[str | None] = mapped_column(String(10))


class PhotoRankingModel(Identity, Base):
    __tablename__ = "photo_ranking_models"
    state: Mapped[str] = mapped_column(String(20), default="queued")
    feature_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    training_choices: Mapped[int] = mapped_column(Integer, default=0)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coefficients: Mapped[list[float] | None] = mapped_column(JSONB)
    metrics: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class PhotoRankingPreference(Base):
    __tablename__ = "photo_ranking_preferences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="deterministic")
