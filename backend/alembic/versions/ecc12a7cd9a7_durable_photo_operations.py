"""durable photo operations

Revision ID: ecc12a7cd9a7
Revises: 1b2a341b45aa
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ecc12a7cd9a7"
down_revision: str | None = "1b2a341b45aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "photo_operation_jobs",
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column(
            "counters", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('archive', 'preview')", name=op.f("ck_photo_operation_jobs_kind")
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'ready', 'failed')",
            name=op.f("ck_photo_operation_jobs_state"),
        ),
        sa.ForeignKeyConstraint(
            ["community_id"],
            ["communities.id"],
            name=op.f("fk_photo_operation_jobs_community_id_communities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_operation_jobs")),
    )
    op.create_index(
        "ix_photo_operation_active",
        "photo_operation_jobs",
        ["community_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('ready', 'failed')"),
    )
    op.create_index(
        "ix_photo_operation_due", "photo_operation_jobs", ["state", "lease_until"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_photo_operation_due", table_name="photo_operation_jobs")
    op.drop_index(
        "ix_photo_operation_active",
        table_name="photo_operation_jobs",
        postgresql_where=sa.text("state NOT IN ('ready', 'failed')"),
    )
    op.drop_table("photo_operation_jobs")
