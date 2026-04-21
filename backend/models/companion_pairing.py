from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text
from sqlmodel import Field

from backend.models.base import BaseDBModel


class CompanionPairingStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class CompanionPairingRevocationReason(str, Enum):
    REPLACED = "replaced"
    MANUAL_UNPAIR = "manual_unpair"
    PENDING_CANCELLED = "pending_cancelled"


class CompanionPairing(BaseDBModel, table=True):
    __tablename__ = "companion_pairings"

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    pairing_session_id: str = Field(
        sa_column=Column(String(64), nullable=False, unique=True, index=True)
    )
    status: str = Field(
        default=CompanionPairingStatus.PENDING.value,
        sa_column=Column(String(16), nullable=False, index=True),
    )
    api_protocol: str = Field(sa_column=Column(String(16), nullable=False))
    api_host: str = Field(sa_column=Column(String(255), nullable=False))
    api_port: int = Field(sa_column=Column(Integer, nullable=False))
    paired_web_origin: str = Field(sa_column=Column(String(2048), nullable=False))
    tls_fingerprint: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    local_control_secret_encrypted: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    local_control_secret_version: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False),
    )
    supersedes_pairing_session_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, index=True),
    )
    revoked_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    revocation_reason: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
    )