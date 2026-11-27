"""A Nojoin account's identity at an OpenID Connect provider.

One row per linked account, matched on ``(issuer, subject)``. ``email``,
``display_name`` and ``groups`` are refreshed at every sign-in through the
provider and shown in the UI; ``groups`` also decides which group shares of a
meeting reach the user (backend/services/recording_access.py).
``created_account`` marks an account the provider's first sign-in created: it
has no usable password and cannot be given one.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.models.base import BaseDBModel


class UserIdentity(BaseDBModel, table=True):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),
    )

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        )
    )
    issuer: str
    subject: str
    email: Optional[str] = Field(default=None, nullable=True)
    display_name: Optional[str] = Field(default=None, nullable=True)
    groups: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )
    created_account: bool = Field(default=False)
    last_sign_in_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime, nullable=True
    )
