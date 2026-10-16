"""drop the recording ASR window results ledger

The live lane and the final pass wrote one row per ASR invocation, behind the
enable_asr_window_result_ledger setting, but since catch-up diarisation was
removed nothing reads the table (issue #428). The rows are advisory history of
transcription attempts; no transcript, utterance or speaker refers to them, and
backup never archived them.

Downgrade recreates the table as d1a4c7b9e2f1 created it, empty: the dropped
rows are not restored.

Revision ID: 3b804f4435a1
Revises: d7e3a1b9c5f2
Create Date: 2026-10-15 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "3b804f4435a1"
down_revision = "d7e3a1b9c5f2"
branch_labels = None
depends_on = None

TABLE = "recording_asr_window_results"
INDEXES = (
    ("ix_recording_asr_window_results_public_id", ["public_id"], True),
    ("ix_recording_asr_window_results_recording_id", ["recording_id"], False),
    (
        "ix_recording_asr_window_results_processing_run_id",
        ["processing_run_id"],
        False,
    ),
    ("ix_recording_asr_window_results_idempotency_key", ["idempotency_key"], False),
)


def upgrade():
    for name, _columns, _unique in reversed(INDEXES):
        op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)


def downgrade():
    op.create_table(
        TABLE,
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.BigInteger(), nullable=False),
        sa.Column("processing_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("span_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("span_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("chunk_start_sequence", sa.BigInteger(), nullable=True),
        sa.Column("chunk_end_sequence", sa.BigInteger(), nullable=True),
        sa.Column("transcription_backend", sa.String(length=255), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("config_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "error_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "produced_utterance_public_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["processing_run_id"], ["processing_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recordings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id",
            "idempotency_key",
            name="uq_recording_asr_window_results_recording_idempotency",
        ),
    )
    for name, columns, unique in INDEXES:
        op.create_index(name, TABLE, columns, unique=unique)
