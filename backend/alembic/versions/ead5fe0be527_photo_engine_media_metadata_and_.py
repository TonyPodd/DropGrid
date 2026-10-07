"""photo engine media metadata and persistent search cache

Revision ID: ead5fe0be527
Revises: df050378e2c3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ead5fe0be527"
down_revision: str | None = "df050378e2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "photo_provider_state",
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("next_request_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interval_seconds", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_photo_provider_state")),
    )
    op.create_table(
        "photo_search_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("cache_key", name=op.f("pk_photo_search_cache")),
    )
    op.create_index(
        op.f("ix_photo_search_cache_expires_at"), "photo_search_cache", ["expires_at"], unique=False
    )
    op.create_table(
        "media_provider_imports",
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_asset_id", sa.String(length=100), nullable=False),
        sa.Column("media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("creator_name", sa.String(length=200), nullable=False),
        sa.Column("creator_url", sa.Text(), nullable=True),
        sa.Column("license_code", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_provider_imports_media_asset_id_media_assets"),
        ),
        sa.PrimaryKeyConstraint(
            "provider", "provider_asset_id", name=op.f("pk_media_provider_imports")
        ),
    )
    op.create_index(
        op.f("ix_media_provider_imports_media_asset_id"),
        "media_provider_imports",
        ["media_asset_id"],
        unique=False,
    )
    op.create_table(
        "photo_plan_leases",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_photo_plan_leases_campaign_id_campaigns"),
        ),
        sa.PrimaryKeyConstraint("campaign_id", name=op.f("pk_photo_plan_leases")),
    )
    op.add_column("media_assets", sa.Column("provider", sa.String(length=50), nullable=True))
    op.add_column(
        "media_assets", sa.Column("provider_asset_id", sa.String(length=100), nullable=True)
    )
    op.add_column("media_assets", sa.Column("creator_name", sa.String(length=200), nullable=True))
    op.add_column("media_assets", sa.Column("creator_url", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("license_code", sa.String(length=100), nullable=True))
    op.add_column("media_assets", sa.Column("license_name", sa.String(length=200), nullable=True))
    op.add_column("media_assets", sa.Column("license_url", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("attribution_text", sa.Text(), nullable=True))
    op.add_column(
        "media_assets",
        sa.Column(
            "requires_publication_attribution", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.add_column("media_assets", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("media_assets", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("media_assets", sa.Column("mime_type", sa.String(length=100), nullable=True))
    op.add_column("media_assets", sa.Column("byte_size", sa.Integer(), nullable=True))
    op.add_column("media_assets", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("media_assets", sa.Column("perceptual_hash", sa.String(length=16), nullable=True))
    op.create_index(
        op.f("ix_media_assets_perceptual_hash"), "media_assets", ["perceptual_hash"], unique=False
    )
    op.create_index(op.f("ix_media_assets_provider"), "media_assets", ["provider"], unique=False)
    op.create_unique_constraint(
        "uq_media_provider_asset", "media_assets", ["provider", "provider_asset_id"]
    )
    op.create_unique_constraint("uq_media_sha256", "media_assets", ["sha256"])
    # Nullable metadata preserves legacy rows. PostgreSQL UNIQUE permits multiple NULLs.
    op.create_check_constraint(
        op.f("ck_media_assets_width_positive"), "media_assets", "width IS NULL OR width > 0"
    )
    op.create_check_constraint(
        op.f("ck_media_assets_height_positive"), "media_assets", "height IS NULL OR height > 0"
    )
    op.create_check_constraint(
        op.f("ck_media_assets_byte_size_positive"),
        "media_assets",
        "byte_size IS NULL OR byte_size > 0",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_media_assets_width_positive"), "media_assets", type_="check")
    op.drop_constraint(op.f("ck_media_assets_height_positive"), "media_assets", type_="check")
    op.drop_constraint(op.f("ck_media_assets_byte_size_positive"), "media_assets", type_="check")
    op.drop_constraint("uq_media_sha256", "media_assets", type_="unique")
    op.drop_constraint("uq_media_provider_asset", "media_assets", type_="unique")
    op.drop_index(op.f("ix_media_assets_provider"), table_name="media_assets")
    op.drop_index(op.f("ix_media_assets_perceptual_hash"), table_name="media_assets")
    op.drop_column("media_assets", "perceptual_hash")
    op.drop_column("media_assets", "sha256")
    op.drop_column("media_assets", "byte_size")
    op.drop_column("media_assets", "mime_type")
    op.drop_column("media_assets", "height")
    op.drop_column("media_assets", "width")
    op.drop_column("media_assets", "requires_publication_attribution")
    op.drop_column("media_assets", "attribution_text")
    op.drop_column("media_assets", "license_url")
    op.drop_column("media_assets", "license_name")
    op.drop_column("media_assets", "license_code")
    op.drop_column("media_assets", "creator_url")
    op.drop_column("media_assets", "creator_name")
    op.drop_column("media_assets", "provider_asset_id")
    op.drop_column("media_assets", "provider")
    op.drop_table("photo_plan_leases")
    op.drop_index(
        op.f("ix_media_provider_imports_media_asset_id"), table_name="media_provider_imports"
    )
    op.drop_table("media_provider_imports")
    op.drop_index(op.f("ix_photo_search_cache_expires_at"), table_name="photo_search_cache")
    op.drop_table("photo_search_cache")
    op.drop_table("photo_provider_state")
