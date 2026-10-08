"""Community profiles, visual reference photos and optional MediaAsset vectors.

Revision ID: 6c10bf42d819
Revises: 7b91d2ef403a
"""

import sqlalchemy as sa

from alembic import op

revision = "6c10bf42d819"
down_revision = "7b91d2ef403a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_content_profiles",
        sa.Column("community_id", sa.Uuid(), sa.ForeignKey("communities.id"), primary_key=True),
        sa.Column("desired_content", sa.Text()),
        sa.Column("avoid_content", sa.Text()),
        sa.Column("style_notes", sa.Text()),
        sa.Column("reference_target_count", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("archive_reuse_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("archive_reuse_min_age_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("references_last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_lease_token", sa.Uuid()),
        sa.Column("sync_lease_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "reference_target_count > 0 AND reference_target_count <= 300", name="target_bound"
        ),
        sa.CheckConstraint("archive_reuse_min_age_days >= 0", name="reuse_age_nonnegative"),
    )
    op.create_table(
        "community_reference_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("community_id", sa.Uuid(), sa.ForeignKey("communities.id"), nullable=False),
        sa.Column("vk_post_id", sa.BigInteger(), nullable=False),
        sa.Column("vk_photo_owner_id", sa.BigInteger(), nullable=False),
        sa.Column("vk_photo_id", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("storage_key", sa.Text()),
        sa.Column("sha256", sa.CHAR(64)),
        sa.Column("perceptual_hash", sa.String(16)),
        sa.Column("embedding", sa.LargeBinary()),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("embedding_dimensions", sa.Integer()),
        sa.Column("reuse_eligible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "community_id", "vk_photo_owner_id", "vk_photo_id", name="uq_community_reference_photo"
        ),
    )
    op.create_index(
        "ix_community_reference_photos_community_id", "community_reference_photos", ["community_id"]
    )
    op.create_index(
        "ix_community_reference_photos_posted_at",
        "community_reference_photos",
        ["community_id", "posted_at"],
    )
    for name, type_ in [
        ("visual_embedding", sa.LargeBinary()),
        ("visual_embedding_model", sa.String(200)),
        ("visual_embedding_dimensions", sa.Integer()),
    ]:
        op.add_column("media_assets", sa.Column(name, type_, nullable=True))


def downgrade():
    for name in ("visual_embedding", "visual_embedding_model", "visual_embedding_dimensions"):
        op.drop_column("media_assets", name)
    op.drop_table("community_reference_photos")
    op.drop_table("community_content_profiles")
