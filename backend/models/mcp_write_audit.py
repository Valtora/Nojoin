"""The change log of what assistants changed through the MCP connector.

One row per write an assistant asked for that got past argument checks:
done, failed at the provider, refused for the state it found, or uncertain
when a write timed out after it was sent. Dry runs write none. Calendar
writes log here today; the table is named for MCP writes generally so other
tools can log to it later. Rows are the user's own data: they go into
backups, are deleted with the account, and are kept 12 months.

Snapshots (provider, calendar name, event title and start) keep a row
readable after its event or calendar is gone.
"""

from datetime import datetime
from typing import Any, List, Optional

import sqlalchemy as sa
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from backend.models.base import BaseDBModel


class McpWriteAudit(BaseDBModel, table=True):
    __tablename__ = "mcp_write_audit"
    __table_args__ = (
        sa.Index("ix_mcp_write_audit_user_id_created_at", "user_id", "created_at"),
        sa.Index(
            "uq_mcp_write_audit_user_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        ),
    )

    user_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        )
    )
    completed_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    grant_id: Optional[str] = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    client_name: Optional[str] = Field(
        default=None, sa_column=Column(String(256), nullable=True)
    )
    area: str = Field(sa_column=Column(String(32), nullable=False))
    tool: str = Field(sa_column=Column(String(64), nullable=False))
    action: str = Field(sa_column=Column(String(16), nullable=False))
    scope: Optional[str] = Field(
        default=None, sa_column=Column(String(16), nullable=True)
    )
    connection_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_connections.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    calendar_source_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    calendar_event_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    provider: Optional[str] = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    calendar_name: Optional[str] = Field(
        default=None, sa_column=Column(String(255), nullable=True)
    )
    provider_event_key: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )
    event_title: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )
    event_start: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    # [{field, before, after}]; for a cancel, the event's whole last state.
    changes: Optional[Any] = Field(
        default=None, sa_column=Column(sa.JSON, nullable=True)
    )
    # The notifications object the tool returned, addresses included.
    notifications: Optional[Any] = Field(
        default=None, sa_column=Column(sa.JSON, nullable=True)
    )
    # pending, succeeded, failed, refused or uncertain.
    outcome: str = Field(sa_column=Column(String(16), nullable=False))
    error_code: Optional[str] = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    idempotency_key: Optional[str] = Field(
        default=None, sa_column=Column(String(128), nullable=True)
    )
    request_fingerprint: Optional[str] = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    response_snapshot: Optional[Any] = Field(
        default=None, sa_column=Column(sa.JSON, nullable=True)
    )


class McpWriteAuditRead(SQLModel):
    id: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    client_name: Optional[str] = None
    tool: str
    action: str
    scope: Optional[str] = None
    connection_id: Optional[int] = None
    provider: Optional[str] = None
    calendar_name: Optional[str] = None
    event_title: Optional[str] = None
    event_start: Optional[datetime] = None
    outcome: str
    error_code: Optional[str] = None
    # Addresses that were emailed, with the kind of mail each got.
    emailed: List[dict[str, Any]] = Field(default_factory=list)
    notifications_summary: Optional[str] = None


class McpWriteAuditPage(SQLModel):
    items: List[McpWriteAuditRead]
    total: int
    skip: int
    limit: int
