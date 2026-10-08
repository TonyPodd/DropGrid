"""Add receipt identities, publication monitoring leases and notification cursor.

Revision ID: 7b91d2ef403a
Revises: ead5fe0be527
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "7b91d2ef403a"
down_revision = "ead5fe0be527"
branch_labels = None
depends_on = None

IDENTITIES = (
    "vk_suggested_post_id",
    "vk_canonical_photo_owner_id",
    "vk_canonical_photo_id",
    "vk_audio_owner_id",
    "vk_audio_id",
    "vk_published_post_id",
)
TIMES = (
    "vk_suggested_at",
    "vk_last_checked_at",
    "vk_next_check_at",
    "vk_publication_detected_at",
    "vk_monitor_lease_until",
)


def upgrade():
    for name in IDENTITIES:
        op.add_column("submissions", sa.Column(name, sa.BigInteger(), nullable=True))
    for name in TIMES:
        op.add_column("submissions", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("submissions", sa.Column("vk_monitor_lease_token", sa.Uuid(), nullable=True))
    op.add_column(
        "submissions", sa.Column("vk_monitor_evidence", postgresql.JSONB(), nullable=True)
    )
    op.create_index("ix_submissions_monitor_due", "submissions", ["status", "vk_next_check_at"])
    op.create_index(
        "ix_submissions_vk_receipt",
        "submissions",
        ["account_id", "vk_suggested_post_id", "community_id"],
    )
    op.create_table(
        "vk_notification_cursors",
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
        sa.Column("next_poll_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_vk_notification_cursors_next_poll_at", "vk_notification_cursors", ["next_poll_at"]
    )


def downgrade():
    op.drop_table("vk_notification_cursors")
    op.drop_index("ix_submissions_vk_receipt", table_name="submissions")
    op.drop_index("ix_submissions_monitor_due", table_name="submissions")
    for name in (*IDENTITIES, *TIMES, "vk_monitor_lease_token", "vk_monitor_evidence"):
        op.drop_column("submissions", name)
