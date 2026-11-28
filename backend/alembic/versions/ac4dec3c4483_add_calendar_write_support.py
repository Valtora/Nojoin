"""Add calendar write support.

Events keep the zone the provider wrote them in, the span they cover
(``range_start``/``range_end``, which ordering, overlap filters and free/busy
read), whether they show as busy or free, the organiser, the user's own answer,
the series an occurrence belongs to, and the provider's revision. Connections
gain ``provider_secrets_encrypted`` for the secrets a provider needs beside its
tokens (Proton's session UID and key passphrase).

Backfill: a timed event's range is its start and end. An all-day event's is
midnight UTC at its dates, which the next full sync moves to midnight in the
calendar's zone. Every calendar's sync cursor is cleared so that next sync is a
full one and fills the new columns, as 96b9c4f5d2aa did for locations.

Downgrade removes Proton connections first, since an older build cannot read
them.

Revision ID: ac4dec3c4483
Revises: 7c1d2e9a4b60
"""

import sqlalchemy as sa
from alembic import op

revision = "ac4dec3c4483"
down_revision = "7c1d2e9a4b60"
branch_labels = None
depends_on = None


def _add_event_columns() -> None:
    columns = [
        sa.Column("start_time_zone", sa.String(length=64), nullable=True),
        sa.Column("end_time_zone", sa.String(length=64), nullable=True),
        sa.Column("range_start", sa.DateTime(), nullable=True),
        sa.Column("range_end", sa.DateTime(), nullable=True),
        sa.Column(
            "show_as", sa.String(length=16), nullable=False, server_default="busy"
        ),
        sa.Column("organiser", sa.JSON(), nullable=True),
        sa.Column("is_organiser", sa.Boolean(), nullable=True),
        sa.Column("my_response", sa.String(length=16), nullable=True),
        sa.Column("series_key", sa.String(length=512), nullable=True),
        sa.Column("original_start", sa.DateTime(), nullable=True),
        sa.Column("original_start_date", sa.Date(), nullable=True),
        sa.Column(
            "occurrence_type",
            sa.String(length=16),
            nullable=False,
            server_default="single",
        ),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column("provider_revision", sa.String(length=255), nullable=True),
        sa.Column("provider_data", sa.JSON(), nullable=True),
    ]
    for column in columns:
        op.add_column("calendar_events", column)


def _backfill_event_ranges() -> None:
    op.execute(
        "UPDATE calendar_events SET range_start = starts_at,"
        " range_end = COALESCE(ends_at, starts_at)"
        " WHERE starts_at IS NOT NULL"
    )
    op.execute(
        "UPDATE calendar_events SET range_start = start_date::timestamp,"
        " range_end = COALESCE(end_date, start_date + 1)::timestamp"
        " WHERE starts_at IS NULL AND start_date IS NOT NULL"
    )
    # No provider writes a row with neither, but the column must not be null.
    op.execute(
        "UPDATE calendar_events SET range_start = created_at, range_end = created_at"
        " WHERE range_start IS NULL"
    )
    op.alter_column("calendar_events", "range_start", nullable=False)
    op.alter_column("calendar_events", "range_end", nullable=False)


def upgrade() -> None:
    _add_event_columns()
    _backfill_event_ranges()
    op.create_index(
        "ix_calendar_events_calendar_id_range_start",
        "calendar_events",
        ["calendar_id", "range_start"],
    )
    op.create_index("ix_calendar_events_series_key", "calendar_events", ["series_key"])
    op.add_column(
        "calendar_connections",
        sa.Column("provider_secrets_encrypted", sa.Text(), nullable=True),
    )
    op.execute("UPDATE calendar_sources SET sync_cursor = NULL")


def downgrade() -> None:
    op.execute("DELETE FROM calendar_connections WHERE provider = 'proton'")
    op.drop_column("calendar_connections", "provider_secrets_encrypted")
    op.drop_index("ix_calendar_events_series_key", table_name="calendar_events")
    op.drop_index(
        "ix_calendar_events_calendar_id_range_start", table_name="calendar_events"
    )
    for column in (
        "provider_data",
        "provider_revision",
        "recurrence_rule",
        "occurrence_type",
        "original_start_date",
        "original_start",
        "series_key",
        "my_response",
        "is_organiser",
        "organiser",
        "show_as",
        "range_end",
        "range_start",
        "end_time_zone",
        "start_time_zone",
    ):
        op.drop_column("calendar_events", column)
