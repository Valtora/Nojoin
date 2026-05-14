from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text
from sqlmodel import Field

from backend.models.base import BaseDBModel


class CompanionPairingRequestStatus(str, Enum):
    PENDING = "pending"
    OPENED = "opened"
    COMPLETING = "completing"
    COMPLETED = "completed"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class CompanionPairingRequest(BaseDBModel, table=True):
    __tablename__ = "companion_pairing_requests"

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    request_id: str = Field(
        sa_column=Column(String(64), nullable=False, unique=True, index=True)
    )
    request_secret_hash: str = Field(sa_column=Column(String(128), nullable=False))
    status: str = Field(
        default=CompanionPairingRequestStatus.PENDING.value,
        sa_column=Column(String(16), nullable=False, index=True),
    )
    api_protocol: str = Field(sa_column=Column(String(16), nullable=False))
    api_host: str = Field(sa_column=Column(String(255), nullable=False))
    api_port: int = Field(sa_column=Column(Integer, nullable=False))
    paired_web_origin: str = Field(sa_column=Column(String(2048), nullable=False))
    replacement_pairing_session_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, index=True),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))
    opened_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
    )
    status_detail: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    failure_reason: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    completed_pairing_session_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, index=True),
    )