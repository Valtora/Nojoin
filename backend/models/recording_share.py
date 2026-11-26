"""Who else may open a recording, and at what level.

The owner (``recordings.user_id``) is never stored here. Each row grants one
user, or every member of one identity-provider group, ``view`` or ``edit``
access; the effective level is the highest that applies
(backend/services/recording_access.py). Exactly one of ``user_id`` and
``group_name`` is set.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, text
from sqlmodel import Field

from backend.models.base import BaseDBModel


class ShareLevel(str, Enum):
    VIEW = "view"
    EDIT = "edit"


class RecordingShare(BaseDBModel, table=True):
    __tablename__ = "recording_shares"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) <> (group_name IS NULL)",
            name="ck_recording_shares_one_grantee",
        ),
        CheckConstraint("level IN ('view', 'edit')", name="ck_recording_shares_level"),
        Index(
            "uq_recording_shares_user",
            "recording_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_recording_shares_group",
            "recording_id",
            "group_name",
            unique=True,
            postgresql_where=text("group_name IS NOT NULL"),
            sqlite_where=text("group_name IS NOT NULL"),
        ),
        Index("ix_recording_shares_user_id", "user_id"),
        Index("ix_recording_shares_group_name", "group_name"),
    )

    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
    )
    group_name: Optional[str] = Field(default=None, nullable=True)
    level: str = Field(max_length=8)
    created_by_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
