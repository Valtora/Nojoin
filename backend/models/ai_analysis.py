"""The AI analytics tier's run log, which the per-user daily budget counts.

One row per dispatched run, written when the run is queued rather than when
it finishes: the budget is a cap on what a user starts, and a run that fails
upstream has still spent a request against the provider. Rows outlive the
recording they were for so a day's count cannot be reset by deleting
meetings.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import Field

from backend.models.base import BaseDBModel

RUN_SOURCE_USER = "user"
RUN_SOURCE_AUTO = "auto"


class AiAnalysisRun(BaseDBModel, table=True):
    __tablename__ = "ai_analysis_runs"

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    recording_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    # "user" for the tab's button and the MCP tool, "auto" for a run queued
    # after processing because the owner opted in.
    source: str = Field(default=RUN_SOURCE_USER, max_length=16)
