"""reference density and durable study jobs

Revision ID: 05a7c0fd0fe8
Revises: 014f4eca0f38
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "05a7c0fd0fe8"
down_revision: str | None = "014f4eca0f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_sync_jobs",
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "progress", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_reference_sync_jobs_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["community_id"],
            ["communities.id"],
            name=op.f("fk_reference_sync_jobs_community_id_communities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reference_sync_jobs")),
    )
    op.create_index(
        "ix_reference_sync_jobs_active",
        "reference_sync_jobs",
        ["community_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('ready', 'failed')"),
    )
    op.create_index(
        "ix_reference_sync_jobs_due", "reference_sync_jobs", ["state", "lease_until"], unique=False
    )
    op.add_column(
        "community_content_profiles", sa.Column("preview_lease_token", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "community_content_profiles",
        sa.Column("preview_lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "community_reference_photos",
        sa.Column("reference_role", sa.String(length=12), server_default="core", nullable=False),
    )
    op.add_column(
        "community_reference_photos", sa.Column("reference_density", sa.Float(), nullable=True)
    )
    op.add_column(
        "community_reference_photos",
        sa.Column("reference_nearest_similarity", sa.Float(), nullable=True),
    )
    op.add_column(
        "community_reference_photos",
        sa.Column("reference_duplicate", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("community_reference_photos", "reference_duplicate")
    op.drop_column("community_reference_photos", "reference_nearest_similarity")
    op.drop_column("community_reference_photos", "reference_density")
    op.drop_column("community_reference_photos", "reference_role")
    op.drop_column("community_content_profiles", "preview_lease_until")
    op.drop_column("community_content_profiles", "preview_lease_token")
    op.drop_index("ix_reference_sync_jobs_due", table_name="reference_sync_jobs")
    op.drop_index(
        "ix_reference_sync_jobs_active",
        table_name="reference_sync_jobs",
        postgresql_where=sa.text("state NOT IN ('ready', 'failed')"),
    )
    op.drop_table("reference_sync_jobs")
