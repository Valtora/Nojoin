from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Index
from sqlmodel import Field, Relationship, SQLModel

from .base import BaseDBModel

if TYPE_CHECKING:
    from .recording import Recording


class RecordingTag(BaseDBModel, table=True):
    __tablename__ = "recording_tags"
    recording_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("recordings.id", ondelete="CASCADE"))
    )
    tag_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("tags.id", ondelete="CASCADE"))
    )

    recording: "Recording" = Relationship(back_populates="tags")
    tag: "Tag" = Relationship(back_populates="recordings")


class Tag(BaseDBModel, table=True):
    __tablename__ = "tags"
    # Each user holds a name once, compared exactly. Different users may share
    # one, and a tag with no owner is not held to it.
    __table_args__ = (Index("uq_tags_user_name", "user_id", "name", unique=True),)

    name: str = Field(index=True)
    color: Optional[str] = Field(default=None, description="Color key for UI display")

    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE")),
    )
    parent_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("tags.id", ondelete="CASCADE")),
    )

    recordings: List["RecordingTag"] = Relationship(back_populates="tag")
    parent: Optional["Tag"] = Relationship(
        back_populates="children", sa_relationship_kwargs={"remote_side": "Tag.id"}
    )
    children: List["Tag"] = Relationship(back_populates="parent")


class TagCreate(SQLModel):
    name: str
    color: Optional[str] = None
    parent_id: Optional[int] = None


class TagUpdate(SQLModel):
    name: Optional[str] = None
    color: Optional[str] = None
    parent_id: Optional[int] = None


class TagRead(BaseDBModel):
    name: str
    color: Optional[str] = None
    user_id: Optional[int] = None
    parent_id: Optional[int] = None
