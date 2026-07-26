"""add a description to notes templates

Revision ID: e1a7c93b8d24
Revises: c4e91f7a2d38
Create Date: 2026-07-26 16:00:00.000000

A separate revision rather than an edit to c4e91f7a2d38: that revision has
already been applied on running installations, so a column added to it there
would never be created.

NULL means no description, which is what every template created before this
revision has. The shipped built-in structure describes itself in the UI and has
no row here.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e1a7c93b8d24"
down_revision = "c4e91f7a2d38"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notes_templates",
        sa.Column("description", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("notes_templates", "description")
