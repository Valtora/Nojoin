from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey
from sqlmodel import Field, SQLModel

from backend.models.base import BaseDBModel


class UserTask(BaseDBModel, table=True):
    __tablename__ = "user_tasks"

    title: str = Field(max_length=255)
    due_on: Optional[date] = None
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )
    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )


class UserTaskCreate(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    due_on: Optional[date] = None


class UserTaskUpdate(SQLModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    due_on: Optional[date] = None
    completed: Optional[bool] = None


class UserTaskRead(BaseDBModel):
    title: str
    due_on: Optional[date] = None
    completed_at: Optional[datetime] = None
