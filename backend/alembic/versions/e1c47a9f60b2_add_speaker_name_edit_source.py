"""Record which surface last named a recording speaker

Revision ID: e1c47a9f60b2
Revises: d5b8c30f7a19
Create Date: 2026-08-03 00:00:00.000000

Renaming a speaker relabels every line that speaker holds, so it is the widest
single edit the connector can make to a transcript, and it was the least
visible: the speaker correction event carried the surface in its JSON payload
and the interface never read it.

Worse, the payload was wrong for the connector. ``set_speaker_name`` called the
REST route handler, which hardcoded ``api``, so an assistant's rename recorded
itself as a human web edit. The backfill below therefore cannot be trusted the
way the utterance one could: rows written before that fix say ``api`` whether
or not a person made them.

So this backfills only the honest half. A rename whose payload says something
other than ``api`` is recorded as-is; ``api`` rows are left NULL rather than
asserting a human made a change that may have been an assistant's. NULL renders
unattributed, which is the truth about them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1c47a9f60b2"
down_revision: Union[str, Sequence[str], None] = "d5b8c30f7a19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BACKFILL = """
UPDATE recording_speakers AS rs
   SET name_last_edit_source = sub.source
  FROM (
       SELECT DISTINCT ON (e.target_recording_speaker_id)
              e.target_recording_speaker_id AS recording_speaker_id,
              e.payload->>'source' AS source
         FROM speaker_correction_events AS e
        WHERE e.event_type IN ('rename', 'link_global_speaker')
          AND e.target_recording_speaker_id IS NOT NULL
          AND e.payload->>'source' IS NOT NULL
          AND e.payload->>'source' <> 'api'
        ORDER BY e.target_recording_speaker_id, e.id DESC
  ) AS sub
 WHERE sub.recording_speaker_id = rs.id
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording_speakers",
        sa.Column("name_last_edit_source", sa.String(length=32), nullable=True),
    )
    op.execute(_BACKFILL)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording_speakers", "name_last_edit_source")
