"""add notes templates and notes provenance

Revision ID: c4e91f7a2d38
Revises: d5a71c93e8b2
Create Date: 2026-07-26 10:00:00.000000

Backs the user-editable meeting-notes structure (issue #137).

``notes_templates`` holds both tiers in one table, separated by ``scope``:
install templates (``user_id`` NULL, managed by the owner/admins, visible to
everyone) and personal templates (owned by ``user_id``, private). Personal rows
cascade with their user; install rows outlive every user.

The two ``transcripts`` columns record which template produced a set of notes.
``notes_template_id`` is ``ON DELETE SET NULL`` rather than CASCADE on purpose:
deleting a template must never delete a meeting's notes, and the accompanying
``notes_template_sections`` snapshot keeps the record readable after the row it
pointed at is gone.

No backfill: NULL on an existing transcript means "generated before templates
existed", which is exactly the shipped built-in structure and is displayed as
such.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e91f7a2d38"
down_revision = "d5a71c93e8b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notes_templates",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sections", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="personal"),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("builtin_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_notes_templates_name", "notes_templates", ["name"])
    op.create_index("ix_notes_templates_scope", "notes_templates", ["scope"])
    op.create_index("ix_notes_templates_user_id", "notes_templates", ["user_id"])

    op.add_column(
        "transcripts",
        sa.Column(
            "notes_template_id",
            sa.BigInteger(),
            sa.ForeignKey("notes_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column("notes_template_sections", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("transcripts", "notes_template_sections")
    op.drop_column("transcripts", "notes_template_id")
    op.drop_index("ix_notes_templates_user_id", table_name="notes_templates")
    op.drop_index("ix_notes_templates_scope", table_name="notes_templates")
    op.drop_index("ix_notes_templates_name", table_name="notes_templates")
    op.drop_table("notes_templates")
