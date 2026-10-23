"""read the whole history of instagram profiles once

Revision ID: b2c4d6e8f0a1
Revises: a1b3c5d7e9f0
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c4d6e8f0a1"
down_revision: str | None = "a1b3c5d7e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL for existing profiles: their whole history is read on the next sync.
    op.add_column(
        "instagram_profiles", sa.Column("full_read_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "instagram_profiles", sa.Column("history_cursor", sa.String(length=1024), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("instagram_profiles", "history_cursor")
    op.drop_column("instagram_profiles", "full_read_at")
