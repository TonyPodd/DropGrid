"""deleted campaigns are hidden but kept for photo history

Revision ID: d4e6f8a1b2c3
Revises: b3c1d7e5a9f2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e6f8a1b2c3"
down_revision: str | None = "b3c1d7e5a9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "deleted_at")
