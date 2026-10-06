"""initial schema

Revision ID: df050378e2c3
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "df050378e2c3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("vk_user_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "gender_tag", sa.Enum("male", "female", "unspecified", name="gender_tag"), nullable=True
        ),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", "invalid", name="account_status"),
            nullable=False,
        ),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
    )
    op.create_table(
        "communities",
        sa.Column("vk_group_id", sa.BigInteger(), nullable=True),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column(
            "required_gender_tag",
            sa.Enum("male", "female", "unspecified", name="gender_tag"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_communities")),
        sa.UniqueConstraint("domain", name=op.f("uq_communities_domain")),
    )
    op.create_table(
        "grids",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grids")),
    )
    op.create_table(
        "media_assets",
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "usage_count >= 0", name=op.f("ck_media_assets_usage_count_nonnegative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
    )
    op.create_table(
        "campaigns",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("grid_id", sa.Uuid(), nullable=False),
        sa.Column("track_url", sa.Text(), nullable=False),
        sa.Column("track_owner_id", sa.BigInteger(), nullable=True),
        sa.Column("track_audio_id", sa.BigInteger(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "ready",
                "running",
                "monitoring",
                "completed",
                "failed",
                "cancelled",
                name="campaign_status",
            ),
            nullable=False,
        ),
        sa.Column("publication_check_hours", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_check_hours > 0", name=op.f("ck_campaigns_check_hours_positive")
        ),
        sa.ForeignKeyConstraint(["grid_id"], ["grids.id"], name=op.f("fk_campaigns_grid_id_grids")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(op.f("ix_campaigns_grid_id"), "campaigns", ["grid_id"], unique=False)
    op.create_table(
        "grid_communities",
        sa.Column("grid_id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["community_id"],
            ["communities.id"],
            name=op.f("fk_grid_communities_community_id_communities"),
        ),
        sa.ForeignKeyConstraint(
            ["grid_id"], ["grids.id"], name=op.f("fk_grid_communities_grid_id_grids")
        ),
        sa.PrimaryKeyConstraint("grid_id", "community_id", name=op.f("pk_grid_communities")),
    )
    op.create_table(
        "submissions",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("media_asset_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sending",
                "submitted",
                "published",
                "not_found",
                "failed",
                "skipped",
                name="submission_status",
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_post_url", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_submissions_attempt_count_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_submissions_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_submissions_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["community_id"],
            ["communities.id"],
            name=op.f("fk_submissions_community_id_communities"),
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_submissions_media_asset_id_media_assets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
        sa.UniqueConstraint("campaign_id", "community_id", name="uq_submission_campaign_community"),
    )
    op.create_index(
        op.f("ix_submissions_campaign_id"), "submissions", ["campaign_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_submissions_campaign_id"), table_name="submissions")
    op.drop_table("submissions")
    op.drop_table("grid_communities")
    op.drop_index(op.f("ix_campaigns_grid_id"), table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_table("media_assets")
    op.drop_table("grids")
    op.drop_table("communities")
    op.drop_table("accounts")
    for enum_name in ("submission_status", "campaign_status", "account_status", "gender_tag"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
