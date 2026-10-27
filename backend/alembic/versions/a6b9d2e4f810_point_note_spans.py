"""Retain line time spans on movable timeline notes.

Revision ID: a6b9d2e4f810
Revises: f2a6e1d9c735
"""

from alembic import op

revision = "a6b9d2e4f810"
down_revision = "f2a6e1d9c735"
branch_labels = None
depends_on = None

OLD_SHAPE = (
    "(kind = 'point' AND anchor_ms IS NOT NULL AND anchor_ms >= 0"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
    " OR (kind = 'selection' AND anchor_ms IS NULL"
    " AND start_ms IS NOT NULL AND end_ms IS NOT NULL"
    " AND start_ms >= 0 AND start_ms < end_ms AND quoted_text IS NOT NULL)"
    " OR (kind IN ('head', 'tail') AND anchor_ms IS NULL"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
)
NEW_SHAPE = (
    "(kind = 'point' AND anchor_ms IS NOT NULL AND anchor_ms >= 0"
    " AND quoted_text IS NULL AND ((start_ms IS NULL AND end_ms IS NULL)"
    " OR (start_ms IS NOT NULL AND end_ms IS NOT NULL"
    " AND start_ms = anchor_ms AND start_ms <= end_ms)))"
    " OR (kind = 'selection' AND anchor_ms IS NULL"
    " AND start_ms IS NOT NULL AND end_ms IS NOT NULL"
    " AND start_ms >= 0 AND start_ms < end_ms AND quoted_text IS NOT NULL)"
    " OR (kind IN ('head', 'tail') AND anchor_ms IS NULL"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
)


def _replace_shape(shape: str) -> None:
    with op.batch_alter_table("timeline_notes") as batch:
        batch.drop_constraint("ck_timeline_notes_shape", type_="check")
        batch.create_check_constraint("ck_timeline_notes_shape", shape)


def upgrade() -> None:
    """Allow paired point spans without changing existing notes."""
    _replace_shape(NEW_SHAPE)


def downgrade() -> None:
    """Keep point notes and their anchors, dropping the optional span snapshot."""
    op.execute(
        "UPDATE timeline_notes SET start_ms = NULL, end_ms = NULL WHERE kind = 'point'"
    )
    _replace_shape(OLD_SHAPE)
