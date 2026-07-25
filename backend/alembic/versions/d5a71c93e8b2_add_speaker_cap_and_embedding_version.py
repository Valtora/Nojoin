"""add per-recording speaker cap and voiceprint extraction method version

Revision ID: d5a71c93e8b2
Revises: b3d7e2f14a05
Create Date: 2026-07-25 12:00:00.000000

Two independent columns land together because both serve diarization quality.

``recordings.max_speakers`` is the optional per-recording upper bound. NULL
means auto-detect, which is the default and the unchanged code path.

``embedding_version`` records which voiceprint extraction method produced a
stored embedding. Existing rows are backfilled to 1 (the sliding-window method)
rather than left NULL, so the "stale voiceprint" population is explicit and
countable rather than inferred from an absent value. Cosine similarity is only
meaningful between two embeddings of the same version.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d5a71c93e8b2"
down_revision = "b3d7e2f14a05"
branch_labels = None
depends_on = None

LEGACY_EMBEDDING_METHOD_VERSION = 1


def upgrade():
    op.add_column(
        "recordings",
        sa.Column("max_speakers", sa.Integer(), nullable=True),
    )

    for table in ("global_speakers", "recording_speakers"):
        op.add_column(
            table,
            sa.Column("embedding_version", sa.Integer(), nullable=True),
        )
        # Only rows that actually hold a vector are stamped. A row with no
        # voiceprint has no method to record, and stamping it would make the
        # stale-voiceprint count wrong.
        #
        # Emptiness is tested with a plain jsonb comparison rather than an
        # array-length call. Postgres does not guarantee that AND conditions are
        # evaluated left to right, and these columns hold JSON `null` (not SQL
        # NULL) on rows whose voiceprint was cleared -- so an array-length call
        # here aborts the migration with "cannot get array length of a scalar"
        # however it is guarded. See the regression test in
        # backend/tests/test_startup_migrations.py.
        op.execute(
            sa.text(
                f"UPDATE {table} SET embedding_version = :version "
                f"WHERE embedding IS NOT NULL "
                f"AND jsonb_typeof(embedding) = 'array' "
                f"AND embedding <> '[]'::jsonb"
            ).bindparams(version=LEGACY_EMBEDDING_METHOD_VERSION)
        )


def downgrade():
    op.drop_column("recording_speakers", "embedding_version")
    op.drop_column("global_speakers", "embedding_version")
    op.drop_column("recordings", "max_speakers")
