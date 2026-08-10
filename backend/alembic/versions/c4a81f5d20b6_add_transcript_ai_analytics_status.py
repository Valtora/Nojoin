"""Track the AI analytics tier's own status on the transcript

Revision ID: c4a81f5d20b6
Revises: b7d3e9c15a24
Create Date: 2026-08-10 00:00:00.000000

The AI tier's payload joins the delivery tier's under the existing
``analytics_payload`` column, keyed ``ai``, so no third JSONB column is needed.
Its *status* cannot be shared, which is why these two columns exist.

The measured tier has four states: pending, generating, completed, error. The
AI tier has a fifth, ``unavailable``, meaning no AI provider is configured.
That is a normal state on a healthy install -- the AI tier is optional, and the
deterministic and delivery tiers work without it -- so folding it into ``error``
would report a working installation as broken. Sharing one column would also
mean an AI failure marking measured delivery as failed, and vice versa, when
the two are produced by different lanes from different inputs and fail for
entirely different reasons.

No backfill. Every existing transcript starts at 'pending', which is honest:
nothing has analysed them. Backfilling would mean spending every user's AI
quota at upgrade time on meetings they may never open.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a81f5d20b6"
down_revision: Union[str, Sequence[str], None] = "b7d3e9c15a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transcripts",
        sa.Column(
            "analytics_ai_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column("analytics_ai_error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("transcripts", "analytics_ai_error_message")
    op.drop_column("transcripts", "analytics_ai_status")
