"""An open ask, from someone a meeting is shared with, to bring it back.

When the owner archives a shared meeting or moves it to the bin, the people it
is shared with can only read it, and can ask the owner to unarchive or restore
it (``POST /recordings/{id}/shares/me/restore-request``). The ask is a task in
the owner's list; this row ties that task to the meeting and the kind of ask,
so there is at most one per meeting and kind whatever the meeting is called,
and the task is completed when the meeting comes back.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, UniqueConstraint
from sqlmodel import Field

from backend.models.base import BaseDBModel


class RestoreKind(str, Enum):
    UNARCHIVE = "unarchive"
    RESTORE = "restore"


class RecordingRestoreRequest(BaseDBModel, table=True):
    __tablename__ = "recording_restore_requests"
    __table_args__ = (
        UniqueConstraint(
            "recording_id", "kind", name="uq_recording_restore_requests_recording_kind"
        ),
        CheckConstraint(
            "kind IN ('unarchive', 'restore')",
            name="ck_recording_restore_requests_kind",
        ),
    )

    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    kind: str = Field(max_length=16)
    task_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("user_tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    requested_by_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
