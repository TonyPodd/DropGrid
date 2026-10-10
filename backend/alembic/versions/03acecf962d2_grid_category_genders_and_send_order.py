"""grid category gender distribution and strict send order

Revision ID: 03acecf962d2
Revises: 0e8c65fbe1ca
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "03acecf962d2"
down_revision: str | None = "0e8c65fbe1ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grid_category_genders",
        sa.Column("grid_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=False),
        sa.Column(
            "gender",
            sa.Enum("male", "female", "unisex", name="category_gender"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["grid_id"],
            ["grids.id"],
            name=op.f("fk_grid_category_genders_grid_id_grids"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("grid_id", "category", name=op.f("pk_grid_category_genders")),
    )
    op.add_column("submissions", sa.Column("send_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "send_order")
    op.drop_table("grid_category_genders")
    sa.Enum(name="category_gender").drop(op.get_bind(), checkfirst=True)
