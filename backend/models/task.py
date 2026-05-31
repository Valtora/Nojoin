from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, Relationship, SQLModel, select

from backend.models.base import BaseDBModel
from backend.models.tag import TagRead

if TYPE_CHECKING:
    from backend.models.recording import Recording
    from backend.models.tag import Tag


class UserTask(BaseDBModel, table=True):
    __tablename__ = "user_tasks"

    title: str = Field(max_length=255)
    body: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, index=True),
    )
    archived_at: Optional[datetime] = Field(
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
    tag_links: list["UserTaskTag"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    recording_links: list["UserTaskRecording"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class UserTaskTag(BaseDBModel, table=True):
    __tablename__ = "user_task_tags"
    __table_args__ = (
        UniqueConstraint("task_id", "tag_id", name="unique_user_task_tag"),
    )

    task_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("user_tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    tag_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    task: UserTask = Relationship(back_populates="tag_links")
    tag: "Tag" = Relationship(sa_relationship_kwargs={"lazy": "selectin"})


class UserTaskRecording(BaseDBModel, table=True):
    __tablename__ = "user_task_recordings"
    __table_args__ = (
        UniqueConstraint("task_id", "recording_id", name="unique_user_task_recording"),
    )

    task_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("user_tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    recording_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("recordings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    task: UserTask = Relationship(back_populates="recording_links")
    recording: "Recording" = Relationship(sa_relationship_kwargs={"lazy": "selectin"})


class UserTaskRecordingRead(SQLModel):
    id: str
    name: str
    created_at: datetime
    duration_seconds: Optional[float] = None
    status: str
    is_archived: bool = False
    is_deleted: bool = False


class UserTaskCreate(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    body: Optional[str] = None
    due_at: Optional[datetime] = None
    tag_ids: list[int] = Field(default_factory=list)
    recording_ids: list[str] = Field(default_factory=list)


class UserTaskUpdate(SQLModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    body: Optional[str] = None
    due_at: Optional[datetime] = None
    completed: Optional[bool] = None
    archived: Optional[bool] = None
    tag_ids: Optional[list[int]] = None
    recording_ids: Optional[list[str]] = None


class UserTaskRead(BaseDBModel):
    title: str
    body: Optional[str] = None
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    tags: list[TagRead] = Field(default_factory=list)
    linked_recordings: list[UserTaskRecordingRead] = Field(default_factory=list)


class AsyncTaskOwnership(BaseDBModel, table=True):
    __tablename__ = "async_task_ownerships"

    task_id: str = Field(max_length=255, unique=True, index=True, nullable=False)
    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )


async def register_task_ownership(db: AsyncSession, task_id: str, user_id: int) -> None:
    """Record the ownership of a Celery task."""
    ownership = AsyncTaskOwnership(task_id=task_id, user_id=user_id)
    db.add(ownership)
    await db.commit()


async def check_task_ownership(db: AsyncSession, task_id: str, user) -> bool:
    """
    Check if the user is allowed to query the given task.
    Admins/superusers can access all tasks.
    Standard users can only access tasks they own.
    """
    is_superuser = getattr(user, "is_superuser", False)
    role = getattr(user, "role", "user")
    if is_superuser or role in ("owner", "admin"):
        return True

    stmt = select(AsyncTaskOwnership).where(AsyncTaskOwnership.task_id == task_id)
    result = await db.execute(stmt)
    ownership = result.scalar_one_or_none()

    if ownership is None:
        return False

    return ownership.user_id == user.id
