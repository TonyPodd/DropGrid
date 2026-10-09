"""add durable campaign sender state

Revision ID: 014f4eca0f38
Revises: a7ad2a0cd235
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014f4eca0f38"
down_revision: str | None = "a7ad2a0cd235"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("vk_next_send_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("campaigns", sa.Column("account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_campaigns_account_id_accounts"), "campaigns", "accounts", ["account_id"], ["id"]
    )
    op.add_column("submissions", sa.Column("vk_send_phase", sa.String(length=32), nullable=True))
    op.add_column("submissions", sa.Column("vk_send_guid", sa.Uuid(), nullable=True))
    op.add_column("submissions", sa.Column("vk_send_lease_token", sa.Uuid(), nullable=True))
    op.add_column(
        "submissions", sa.Column("vk_send_lease_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "submissions", sa.Column("vk_send_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "submissions", sa.Column("vk_send_next_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "submissions", sa.Column("vk_send_receipt_post_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "submissions", sa.Column("vk_photo_upload_owner_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("submissions", sa.Column("vk_photo_upload_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "submissions",
        sa.Column("vk_send_readback_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index(
        "ix_submissions_send_due", "submissions", ["status", "vk_send_next_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_send_due", table_name="submissions")
    op.drop_column("submissions", "vk_send_readback_attempts")
    op.drop_column("submissions", "vk_photo_upload_id")
    op.drop_column("submissions", "vk_photo_upload_owner_id")
    op.drop_column("submissions", "vk_send_receipt_post_id")
    op.drop_column("submissions", "vk_send_next_at")
    op.drop_column("submissions", "vk_send_started_at")
    op.drop_column("submissions", "vk_send_lease_until")
    op.drop_column("submissions", "vk_send_lease_token")
    op.drop_column("submissions", "vk_send_guid")
    op.drop_column("submissions", "vk_send_phase")
    op.drop_constraint(op.f("fk_campaigns_account_id_accounts"), "campaigns", type_="foreignkey")
    op.drop_column("campaigns", "account_id")
    op.drop_column("accounts", "vk_next_send_at")
