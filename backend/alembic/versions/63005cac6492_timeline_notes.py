"""timeline notes replace the transcript's free-text user notes

A recording's notes become rows of their own, anchored in milliseconds on the
recording's audio timeline (backend/models/timeline_note.py). They hang off the
recording rather than the transcript, because a reprocess deletes the
transcript row and the final pass rebuilds every utterance.

Upgrade turns each non-blank transcripts.user_notes into one 'head' note: the
note shown before the transcript and edited by the same textarea, which the
prompts read in the same place the free text used to sit. The note is
attributed to the recording's owner and dated by the transcript's last update.
The column is then dropped: every reader is rewritten in the same change.

Each note keeps the client-chosen token of the write that made its revision
(last_write_token), and a delete leaves a row in timeline_note_tombstones with
the note's id and the delete's token, so a write still in flight for a deleted
note hears whose delete it was instead of re-creating the note. A tombstone left
by a delete of an id no note held is marked never_held: it only refuses a create
still in transit, and the API purges it once none can be.

Downgrade folds each recording's notes back into user_notes, head notes first,
then the anchored notes in timeline order, then tail notes, separated by blank
lines, creating the transcript row where none exists. It is lossy: anchors,
quotes, authors, per-note timestamps, write tokens and tombstones are dropped.

Revision ID: 63005cac6492
Revises: 3b804f4435a1
Create Date: 2026-10-17 00:00:00.000000

"""

import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "63005cac6492"
down_revision = "3b804f4435a1"
branch_labels = None
depends_on = None

TABLE = "timeline_notes"
INDEXES = (
    ("ix_timeline_notes_public_id", ["public_id"], True),
    ("ix_timeline_notes_recording_id", ["recording_id"], False),
    ("ix_timeline_notes_created_by_user_id", ["created_by_user_id"], False),
)
TOMBSTONES = "timeline_note_tombstones"
TOMBSTONE_INDEXES = (
    ("ix_timeline_note_tombstones_public_id", ["public_id"], True),
    ("ix_timeline_note_tombstones_recording_id", ["recording_id"], False),
)

# Copied rather than imported from the model: a migration must keep meaning
# what it meant when it was written.
KIND_CHECK = "kind IN ('point', 'selection', 'head', 'tail')"
SHAPE_CHECK = (
    "(kind = 'point' AND anchor_ms IS NOT NULL AND anchor_ms >= 0"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
    " OR (kind = 'selection' AND anchor_ms IS NULL"
    " AND start_ms IS NOT NULL AND end_ms IS NOT NULL"
    " AND start_ms >= 0 AND start_ms < end_ms AND quoted_text IS NOT NULL)"
    " OR (kind IN ('head', 'tail') AND anchor_ms IS NULL"
    " AND start_ms IS NULL AND end_ms IS NULL AND quoted_text IS NULL)"
)

_notes = sa.table(
    TABLE,
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
    sa.column("public_id", sa.String()),
    sa.column("recording_id", sa.BigInteger()),
    sa.column("created_by_user_id", sa.BigInteger()),
    sa.column("kind", sa.String()),
    sa.column("body", sa.Text()),
    sa.column("revision", sa.Integer()),
)


def upgrade():
    op.create_table(
        TABLE,
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("anchor_ms", sa.BigInteger(), nullable=True),
        sa.Column("start_ms", sa.BigInteger(), nullable=True),
        sa.Column("end_ms", sa.BigInteger(), nullable=True),
        sa.Column("quoted_text", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_write_token", sa.String(length=36), nullable=True),
        sa.CheckConstraint(KIND_CHECK, name="ck_timeline_notes_kind"),
        sa.CheckConstraint(SHAPE_CHECK, name="ck_timeline_notes_shape"),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns, unique in INDEXES:
        op.create_index(name, TABLE, columns, unique=unique)

    op.create_table(
        TOMBSTONES,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("deleted_write_token", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.Column(
            "never_held", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns, unique in TOMBSTONE_INDEXES:
        op.create_index(name, TOMBSTONES, columns, unique=unique)

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT t.recording_id, t.user_notes, t.updated_at, r.user_id "
            "FROM transcripts t JOIN recordings r ON r.id = t.recording_id "
            "WHERE t.user_notes IS NOT NULL"
        )
    ).all()
    # Stripped in Python, as the API stored them: btrim() trims spaces only, so
    # a note of nothing but newlines would otherwise survive as a blank note.
    head_notes = [
        {
            "created_at": updated_at,
            "updated_at": updated_at,
            "public_id": str(uuid.uuid4()),
            "recording_id": recording_id,
            "created_by_user_id": owner_id,
            "kind": "head",
            "body": user_notes.strip(),
            "revision": 1,
        }
        for recording_id, user_notes, updated_at, owner_id in rows
        if user_notes.strip()
    ]
    if head_notes:
        op.bulk_insert(_notes, head_notes)

    op.drop_column("transcripts", "user_notes")


def downgrade():
    op.add_column("transcripts", sa.Column("user_notes", sa.Text(), nullable=True))

    folded = (
        "SELECT recording_id, string_agg(body, E'\\n\\n' ORDER BY"
        " CASE kind WHEN 'head' THEN 0 WHEN 'tail' THEN 2 ELSE 1 END,"
        " CASE kind WHEN 'point' THEN anchor_ms"
        " WHEN 'selection' THEN GREATEST(start_ms, end_ms - 1) END NULLS FIRST,"
        # Ties break as the app orders notes: creation, then public id.
        " created_at, public_id) AS user_notes"
        # A body of only whitespace cannot come from the API, but would fold
        # into blank lines.
        f" FROM {TABLE} WHERE body ~ '\\S' GROUP BY recording_id"
    )
    op.execute(
        f"UPDATE transcripts SET user_notes = folded.user_notes FROM ({folded}) folded"
        " WHERE transcripts.recording_id = folded.recording_id"
    )
    # Before this revision a note could only live on a transcript row, and the
    # notes endpoint created one with these defaults when there was none.
    op.execute(
        "INSERT INTO transcripts (created_at, updated_at, recording_id, user_notes,"
        " notes_status, transcript_status, meeting_edge_status,"
        " speaker_name_suggestions)"
        " SELECT now(), now(), folded.recording_id, folded.user_notes,"
        " 'pending', 'pending', 'idle', '[]'::jsonb"
        f" FROM ({folded}) folded"
        " WHERE NOT EXISTS (SELECT 1 FROM transcripts t"
        " WHERE t.recording_id = folded.recording_id)"
    )

    for name, _columns, _unique in reversed(TOMBSTONE_INDEXES):
        op.drop_index(name, table_name=TOMBSTONES)
    op.drop_table(TOMBSTONES)
    for name, _columns, _unique in reversed(INDEXES):
        op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
