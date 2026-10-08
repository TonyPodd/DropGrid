"""Archive windows, lazy discovery metadata, source provenance and usage cooldown.

Revision ID: 8a40d29be731
Revises: 6c10bf42d819
"""

import sqlalchemy as sa

from alembic import op

revision = "8a40d29be731"
down_revision = "6c10bf42d819"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "community_content_profiles",
        sa.Column("archive_reuse_max_age_days", sa.Integer(), nullable=False, server_default="540"),
    )
    op.execute(
        "UPDATE community_content_profiles SET archive_reuse_max_age_days = "
        "GREATEST(540, archive_reuse_min_age_days + 1)"
    )
    op.create_check_constraint(
        "reuse_window",
        "community_content_profiles",
        "archive_reuse_max_age_days > archive_reuse_min_age_days "
        "AND archive_reuse_max_age_days <= 3650",
    )
    for name, typ in (
        ("archive_last_synced_at", sa.DateTime(timezone=True)),
        ("archive_lease_token", sa.Uuid()),
        ("archive_lease_until", sa.DateTime(timezone=True)),
    ):
        op.add_column("community_content_profiles", sa.Column(name, typ))
    for name, default in (
        ("is_style_reference", "true"),
        ("archive_discovered", "false"),
        ("enabled", "true"),
    ):
        op.add_column(
            "community_reference_photos",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=default),
        )
    for name in ("width", "height"):
        op.add_column("community_reference_photos", sa.Column(name, sa.Integer()))
    op.add_column(
        "media_provider_imports",
        sa.Column("source_community_id", sa.Uuid(), sa.ForeignKey("communities.id")),
    )
    op.add_column("media_provider_imports", sa.Column("source_post_id", sa.BigInteger()))
    op.create_index(
        "ix_media_provider_imports_source_community_id",
        "media_provider_imports",
        ["source_community_id"],
    )
    op.create_table(
        "community_media_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("community_id", sa.Uuid(), sa.ForeignKey("communities.id"), nullable=False),
        sa.Column("media_asset_id", sa.Uuid(), sa.ForeignKey("media_assets.id")),
        sa.Column("source_provider", sa.String(50), nullable=False),
        sa.Column("source_identity", sa.String(100), nullable=False),
        sa.Column("sha256", sa.CHAR(64)),
        sa.Column("perceptual_hash", sa.String(16)),
        sa.Column("first_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("last_submission_id", sa.Uuid(), sa.ForeignKey("submissions.id")),
        sa.UniqueConstraint(
            "community_id",
            "source_provider",
            "source_identity",
            name="uq_community_media_usage_source",
        ),
        sa.CheckConstraint("use_count > 0", name="use_count_positive"),
    )
    op.create_index(
        "ix_community_media_usage_recent", "community_media_usage", ["community_id", "last_used_at"]
    )
    # Actual DropGrid receipts, never original VK wall appearances, seed the ledger.
    op.execute("""INSERT INTO community_media_usage
      (id,created_at,community_id,media_asset_id,source_provider,source_identity,
       sha256,perceptual_hash,first_used_at,last_used_at,use_count)
      SELECT gen_random_uuid(),now(),s.community_id,m.id,COALESCE(m.provider,'library'),
       COALESCE(m.provider_asset_id,m.id::text),m.sha256,m.perceptual_hash,
       min(s.submitted_at),max(s.submitted_at),count(*)
      FROM submissions s JOIN media_assets m ON m.id=s.media_asset_id
      WHERE s.submitted_at IS NOT NULL AND s.vk_suggested_post_id IS NOT NULL
      GROUP BY s.community_id,m.id""")


def downgrade():
    op.drop_table("community_media_usage")
    op.drop_index(
        "ix_media_provider_imports_source_community_id", table_name="media_provider_imports"
    )
    op.drop_column("media_provider_imports", "source_post_id")
    op.drop_column("media_provider_imports", "source_community_id")
    for name in ("height", "width", "enabled", "archive_discovered", "is_style_reference"):
        op.drop_column("community_reference_photos", name)
    op.drop_constraint(
        op.f("ck_community_content_profiles_reuse_window"),
        "community_content_profiles",
        type_="check",
    )
    for name in (
        "archive_lease_until",
        "archive_lease_token",
        "archive_last_synced_at",
        "archive_reuse_max_age_days",
    ):
        op.drop_column("community_content_profiles", name)
