from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
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
    vk_user_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    gender_tag: Mapped[GenderTag | None] = mapped_column(Enum(GenderTag, name="gender_tag"))
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status"), default=AccountStatus.active
    )
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)


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


class Grid(Identity, Updated, Base):
    __tablename__ = "grids"
    name: Mapped[str] = mapped_column(String(200))


class GridCommunity(Base):
    __tablename__ = "grid_communities"
    grid_id: Mapped[UUID] = mapped_column(ForeignKey("grids.id"), primary_key=True)
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"), primary_key=True)
    # Keep the import category per grid without overwriting another grid's category.
    category: Mapped[str | None] = mapped_column(String(200))


class MediaAsset(Identity, Base):
    __tablename__ = "media_assets"
    __table_args__ = (CheckConstraint("usage_count >= 0", name="usage_count_nonnegative"),)
    storage_key: Mapped[str] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


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
    publication_check_hours: Mapped[int] = mapped_column(Integer, default=72)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Submission(Identity, Updated, Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "community_id", name="uq_submission_campaign_community"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
    )
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    community_id: Mapped[UUID] = mapped_column(ForeignKey("communities.id"))
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"))
    media_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"), default=SubmissionStatus.pending
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_post_url: Mapped[str | None] = mapped_column(Text)
