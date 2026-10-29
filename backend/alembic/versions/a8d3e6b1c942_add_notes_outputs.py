"""Add extra notes outputs without moving the primary notes.

Revision ID: a8d3e6b1c942
Revises: f2a6e1d9c735
"""

import sqlalchemy as sa
from alembic import op

revision = "a8d3e6b1c942"
down_revision = "f2a6e1d9c735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notes_outputs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "recording_id",
            sa.BigInteger(),
            sa.ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False, server_default="notes"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "notes_template_id",
            sa.BigInteger(),
            sa.ForeignKey("notes_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes_template_sections", sa.Text(), nullable=True),
        sa.Column("notes_status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_notes_outputs_recording_id", "notes_outputs", ["recording_id"])


def downgrade() -> None:
    op.drop_table("notes_outputs")
