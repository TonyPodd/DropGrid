"""campaign choice to suggest posts after preparation

Revision ID: b3c1d7e5a9f2
Revises: 8f5e31ebf9a6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3c1d7e5a9f2"
down_revision: str | None = "8f5e31ebf9a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("auto_send", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "auto_send")
