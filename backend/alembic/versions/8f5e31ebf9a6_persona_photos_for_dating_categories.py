"""girl photo sets for dating/selfie categories and community descriptions

Revision ID: 8f5e31ebf9a6
Revises: 627da5cc2ebc
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8f5e31ebf9a6"
down_revision: str | None = "627da5cc2ebc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_persona_photos",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('selfie', 'other')", name=op.f("ck_account_persona_photos_kind_known")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_account_persona_photos_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_assets.id"],
            name=op.f("fk_account_persona_photos_media_asset_id_media_assets"),
        ),
        sa.PrimaryKeyConstraint(
            "account_id", "media_asset_id", name=op.f("pk_account_persona_photos")
        ),
        sa.UniqueConstraint("media_asset_id", name="uq_account_persona_photos_media_asset_id"),
    )
    op.add_column(
        "campaign_accounts",
        sa.Column("persona", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("communities", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("communities", "description")
    op.drop_column("campaign_accounts", "persona")
    op.drop_table("account_persona_photos")
