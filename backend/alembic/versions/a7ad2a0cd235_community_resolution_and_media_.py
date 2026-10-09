"""community resolution and media preparation queue

Revision ID: a7ad2a0cd235
Revises: 13b9a740d812
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7ad2a0cd235"
down_revision: str | None = "13b9a740d812"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_preparation_jobs",
        sa.Column("grid_id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'ready', 'failed', 'transient')",
            name=op.f("ck_media_preparation_jobs_state"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_media_preparation_jobs_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["community_id"],
            ["communities.id"],
            name=op.f("fk_media_preparation_jobs_community_id_communities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grid_id"],
            ["grids.id"],
            name=op.f("fk_media_preparation_jobs_grid_id_grids"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_preparation_jobs")),
        sa.UniqueConstraint("grid_id", "community_id", "account_id", name="uq_media_prep_context"),
    )
    op.create_index(
        "ix_media_prep_claim", "media_preparation_jobs", ["state", "lease_until"], unique=False
    )
    op.add_column(
        "communities",
        sa.Column(
            "resolution_status", sa.String(length=32), server_default="unresolved", nullable=False
        ),
    )
    op.add_column(
        "communities", sa.Column("resolution_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("communities", sa.Column("resolution_error_code", sa.Integer(), nullable=True))
    op.add_column(
        "grid_communities", sa.Column("source_reference", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("grid_communities", "source_reference")
    op.drop_column("communities", "resolution_error_code")
    op.drop_column("communities", "resolution_checked_at")
    op.drop_column("communities", "resolution_status")
    op.drop_index("ix_media_prep_claim", table_name="media_preparation_jobs")
    op.drop_table("media_preparation_jobs")
