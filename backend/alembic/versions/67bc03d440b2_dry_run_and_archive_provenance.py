"""immutable dry-run scope and canonical archive import provenance

Revision ID: 67bc03d440b2
Revises: 14de7a34ff11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "67bc03d440b2"
down_revision = "14de7a34ff11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns", sa.Column("is_dry_run", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column("campaigns", sa.Column("dry_run_scope", postgresql.JSONB(), nullable=True))
    for name, kind in (
        ("source_photo_owner_id", sa.BigInteger()),
        ("source_photo_id", sa.BigInteger()),
        ("source_posted_at", sa.DateTime(timezone=True)),
        ("source_sha256", sa.CHAR(64)),
        ("source_perceptual_hash", sa.String(16)),
        ("source_embedding", sa.Text()),
        ("source_embedding_model", sa.String(100)),
        ("source_embedding_dimensions", sa.Integer()),
    ):
        op.add_column("media_provider_imports", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name in (
        "source_embedding_dimensions",
        "source_embedding_model",
        "source_embedding",
        "source_perceptual_hash",
        "source_sha256",
        "source_posted_at",
        "source_photo_id",
        "source_photo_owner_id",
    ):
        op.drop_column("media_provider_imports", name)
    op.drop_column("campaigns", "dry_run_scope")
    op.drop_column("campaigns", "is_dry_run")
