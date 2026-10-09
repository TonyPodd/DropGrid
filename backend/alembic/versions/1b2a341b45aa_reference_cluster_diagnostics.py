"""reference cluster diagnostics

Revision ID: 1b2a341b45aa
Revises: 8fcbaee3515d
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1b2a341b45aa"
down_revision: str | None = "8fcbaee3515d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "community_reference_photos",
        sa.Column("reference_cluster_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "community_reference_photos",
        sa.Column(
            "reference_role_reason", sa.String(length=40), server_default="density", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("community_reference_photos", "reference_role_reason")
    op.drop_column("community_reference_photos", "reference_cluster_size")
