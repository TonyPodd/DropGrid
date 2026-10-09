"""Photo import provenance and community feedback (no ranking influence)."""

import sqlalchemy as sa

from alembic import op

revision = "42a4c1b530ea"
down_revision = "ecc12a7cd9a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_provider_imports", sa.Column("image_source_url", sa.Text(), nullable=True))
    op.add_column(
        "media_provider_imports", sa.Column("retrieval_query", sa.String(100), nullable=True)
    )
    op.create_table(
        "community_photo_feedback",
        sa.Column(
            "community_id",
            sa.UUID(),
            sa.ForeignKey("communities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(50), primary_key=True),
        sa.Column("source_identity", sa.String(100), primary_key=True),
        sa.Column("rating", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating IN ('like', 'dislike')", name="feedback_rating"),
    )


def downgrade():
    op.drop_table("community_photo_feedback")
    op.drop_column("media_provider_imports", "retrieval_query")
    op.drop_column("media_provider_imports", "image_source_url")
