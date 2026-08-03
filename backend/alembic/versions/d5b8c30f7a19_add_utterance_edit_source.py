"""Record which surface authored an utterance's manual edit

Revision ID: d5b8c30f7a19
Revises: c9e4a25b71f3
Create Date: 2026-08-03 00:00:00.000000

The event log already recorded whether a correction came from the web app or
from a connected assistant, but ``transcript_utterances`` collapsed that to a
pair of booleans, so the interface could only say an utterance had been edited,
never by what. These two columns denormalise the answer onto the row the read
model already loads, mirroring ``speaker_assignment_source`` beside them.

The backfill replays ``transcript_utterance_events`` rather than inventing a
value: those rows already carry ``api`` or ``mcp`` and have since the connector
shipped. Only locked utterances are attributed, because the lock is what the
source describes. An utterance locked with no matching edit event stays NULL --
that is every row the phase-1 import locked from a legacy JSON blob, and NULL
is the honest answer for them rather than a guess at "manual".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b8c30f7a19"
down_revision: Union[str, Sequence[str], None] = "c9e4a25b71f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BACKFILL = """
UPDATE transcript_utterances AS u
   SET {column} = (
       SELECT e.source
         FROM transcript_utterance_events AS e
        WHERE e.utterance_id = u.id
          AND e.event_type = '{event_type}'
        ORDER BY e.id DESC
        LIMIT 1
   )
 WHERE u.{lock_column} IS TRUE
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transcript_utterances",
        sa.Column("text_last_edit_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "transcript_utterances",
        sa.Column("speaker_last_edit_source", sa.String(length=32), nullable=True),
    )

    op.execute(
        _BACKFILL.format(
            column="text_last_edit_source",
            event_type="update_text",
            lock_column="manual_text_locked",
        )
    )
    op.execute(
        _BACKFILL.format(
            column="speaker_last_edit_source",
            event_type="update_speaker",
            lock_column="manual_speaker_locked",
        )
    )


def downgrade() -> None:
    """Downgrade schema.

    Dropping the columns loses only derived data: the event log the backfill
    replayed is untouched, so re-applying this migration reconstructs the same
    attribution for every edit made before the downgrade.
    """
    op.drop_column("transcript_utterances", "speaker_last_edit_source")
    op.drop_column("transcript_utterances", "text_last_edit_source")
