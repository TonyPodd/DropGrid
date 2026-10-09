"""experimental Pinterest preview cache

Revision ID: 8fcbaee3515d
Revises: 05a7c0fd0fe8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8fcbaee3515d"
down_revision: str | None = "05a7c0fd0fe8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "photo_preview_cache",
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_asset_id", sa.String(length=100), nullable=False),
        sa.Column("candidate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("perceptual_hash", sa.String(length=16), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_photo_preview_cache")),
        sa.UniqueConstraint("provider", "provider_asset_id", name="uq_preview_pin"),
    )


def downgrade() -> None:
    op.drop_table("photo_preview_cache")
