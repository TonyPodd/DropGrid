"""Per-grid-community operator comments and intentional retrieval hints."""

import sqlalchemy as sa

from alembic import op

revision = "13b9a740d812"
down_revision = "8a40d29be731"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("grid_communities", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column("grid_communities", sa.Column("content_hint", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("grid_communities", "content_hint")
    op.drop_column("grid_communities", "comment")
