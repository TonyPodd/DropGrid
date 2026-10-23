"""instagram profiles as a photo source

Revision ID: a1b3c5d7e9f0
Revises: f6a8b0c2d4e5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b3c5d7e9f0"
down_revision: str | None = "f6a8b0c2d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instagram_profiles",
        sa.Column("username", sa.String(length=30), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("added_by", sa.String(length=16), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("photo_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("centroid", sa.LargeBinary(), nullable=True),
        sa.Column("centroid_model", sa.String(length=200), nullable=True),
        sa.Column("centroid_dimensions", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instagram_profiles")),
        sa.UniqueConstraint("username", name=op.f("uq_instagram_profiles_username")),
    )
    op.create_table(
        "instagram_photos",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.String(length=100), nullable=False),
        sa.Column("permalink", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_instagram_photos_media_asset_id_media_assets"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["instagram_profiles.id"],
            name=op.f("fk_instagram_photos_profile_id_instagram_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instagram_photos")),
        sa.UniqueConstraint("media_id", name=op.f("uq_instagram_photos_media_id")),
    )
    op.create_index(
        op.f("ix_instagram_photos_media_asset_id"), "instagram_photos", ["media_asset_id"]
    )
    op.create_index(op.f("ix_instagram_photos_profile_id"), "instagram_photos", ["profile_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_instagram_photos_profile_id"), table_name="instagram_photos")
    op.drop_index(op.f("ix_instagram_photos_media_asset_id"), table_name="instagram_photos")
    op.drop_table("instagram_photos")
    op.drop_table("instagram_profiles")
