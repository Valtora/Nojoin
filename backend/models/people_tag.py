from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy import BigInteger, ForeignKey, Column, UniqueConstraint
from .base import BaseDBModel

if TYPE_CHECKING:
    from .speaker import GlobalSpeaker

class PeopleTagLink(BaseDBModel, table=True):
    __tablename__ = "people_tags"
    
    global_speaker_id: int = Field(sa_column=Column(BigInteger, ForeignKey("global_speakers.id", ondelete="CASCADE")))
    tag_id: int = Field(sa_column=Column(BigInteger, ForeignKey("p_tags.id", ondelete="CASCADE")))
    
    __table_args__ = (
        UniqueConstraint("global_speaker_id", "tag_id", name="unique_person_tag"),
    )
    
    tag: "PeopleTag" = Relationship(sa_relationship_kwargs={"lazy": "selectin"})

class PeopleTag(BaseDBModel, table=True):
    __tablename__ = "p_tags"
    
    name: str = Field(index=True)
    color: Optional[str] = Field(default=None, description="Color key for UI display")
    
    user_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE")))
    parent_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, ForeignKey("p_tags.id", ondelete="CASCADE")))
    
    links: List["PeopleTagLink"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete", "overlaps": "tag"}
    )
    
    parent: Optional["PeopleTag"] = Relationship(
        back_populates="children", 
        sa_relationship_kwargs={"remote_side": "PeopleTag.id"}
    )
    children: List["PeopleTag"] = Relationship(back_populates="parent")
