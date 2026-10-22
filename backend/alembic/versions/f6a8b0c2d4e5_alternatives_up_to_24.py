"""up to 24 alternatives per community

Revision ID: f6a8b0c2d4e5
Revises: e5f7a9b1c3d4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f6a8b0c2d4e5"
down_revision: str | None = "e5f7a9b1c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_photo_selection_candidates_rank_bounded"),
        "photo_selection_candidates",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_photo_selection_candidates_rank_bounded"),
        "photo_selection_candidates",
        "rank >= 1 AND rank <= 24",
    )


def downgrade() -> None:
    op.execute("DELETE FROM photo_selection_candidates WHERE rank > 12")
    op.drop_constraint(
        op.f("ck_photo_selection_candidates_rank_bounded"),
        "photo_selection_candidates",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_photo_selection_candidates_rank_bounded"),
        "photo_selection_candidates",
        "rank >= 1 AND rank <= 12",
    )
