"""who added each account

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a1b2c3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f7a9b1c3d4"
down_revision: str | None = "d4e6f8a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("owner", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "owner")
