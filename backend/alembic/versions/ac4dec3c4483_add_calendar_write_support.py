"""Add calendar write support.

Events keep the zone the provider wrote them in, the span they cover
(``range_start``/``range_end``, which ordering, overlap filters and free/busy
read), whether they show as busy or free, the organiser, the user's own answer,
the series an occurrence belongs to, and the provider's revision. Connections
gain ``provider_secrets_encrypted`` for the secrets a provider needs beside its
tokens (Proton's session UID and key passphrase).

Two tables: ``calendar_attendee_invites`` records whether Nojoin has invited
each attendee of an event it wrote, and ``mcp_write_audit`` is the change log
of every write an assistant made through the MCP connector, calendar writes
first, kept 12 months.

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


def _create_invites_table() -> None:
    op.create_table(
        "calendar_attendee_invites",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("calendar_source_id", sa.BigInteger(), nullable=False),
        sa.Column("event_uid", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("invited_at", sa.DateTime(), nullable=True),
        sa.Column("invited_sequence", sa.Integer(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["calendar_source_id"], ["calendar_sources.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calendar_source_id",
            "event_uid",
            "email",
            name="uq_calendar_attendee_invite_source_uid_email",
        ),
    )
    op.create_index(
        "ix_calendar_attendee_invites_calendar_source_id",
        "calendar_attendee_invites",
        ["calendar_source_id"],
    )


def _create_audit_table() -> None:
    op.create_table(
        "mcp_write_audit",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("grant_id", sa.String(length=64), nullable=True),
        sa.Column("client_name", sa.String(length=256), nullable=True),
        sa.Column("area", sa.String(length=32), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=True),
        sa.Column("connection_id", sa.BigInteger(), nullable=True),
        sa.Column("calendar_source_id", sa.BigInteger(), nullable=True),
        sa.Column("calendar_event_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("calendar_name", sa.String(length=255), nullable=True),
        sa.Column("provider_event_key", sa.String(length=512), nullable=True),
        sa.Column("event_title", sa.String(length=512), nullable=True),
        sa.Column("event_start", sa.DateTime(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("notifications", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("response_snapshot", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["calendar_connections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["calendar_source_id"], ["calendar_sources.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["calendar_event_id"], ["calendar_events.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mcp_write_audit_user_id_created_at",
        "mcp_write_audit",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_mcp_write_audit_connection_id", "mcp_write_audit", ["connection_id"]
    )
    op.create_index(
        "uq_mcp_write_audit_user_idempotency_key",
        "mcp_write_audit",
        ["user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


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
    _create_invites_table()
    _create_audit_table()


def downgrade() -> None:
    op.drop_table("mcp_write_audit")
    op.drop_table("calendar_attendee_invites")
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
