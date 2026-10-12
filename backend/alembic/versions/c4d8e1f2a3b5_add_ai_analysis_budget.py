"""add the AI analysis run log and the per-user daily limit

The AI analytics tier spends the user's own provider quota, so an
administrator can now cap how many runs a user starts per UTC day. Runs are
logged in ai_analysis_runs when they are queued, and users gains a nullable
per-user limit that overrides the install-wide default.

Revision ID: c4d8e1f2a3b5
Revises: b182c2378829
Create Date: 2026-10-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4d8e1f2a3b5"
down_revision = "b182c2378829"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_analysis_runs",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_analysis_runs_user_id"),
        "ai_analysis_runs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_analysis_runs_recording_id"),
        "ai_analysis_runs",
        ["recording_id"],
        unique=False,
    )
    op.add_column(
        "users",
        sa.Column("ai_analysis_daily_limit", sa.Integer(), nullable=True),
    )


def downgrade():
    # The run log is the only record of today's usage, so a downgrade resets
    # every user's count for the day; the cap itself is config and survives.
    op.drop_column("users", "ai_analysis_daily_limit")
    op.drop_index(
        op.f("ix_ai_analysis_runs_recording_id"), table_name="ai_analysis_runs"
    )
    op.drop_index(op.f("ix_ai_analysis_runs_user_id"), table_name="ai_analysis_runs")
    op.drop_table("ai_analysis_runs")
