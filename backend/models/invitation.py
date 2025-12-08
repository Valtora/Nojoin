from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import ForeignKey, Column, Integer
from sqlmodel import Field, Relationship
from backend.models.base import BaseDBModel
import uuid

if TYPE_CHECKING:
    from backend.models.user import User

class Invitation(BaseDBModel, table=True):
    __tablename__ = "invitations"
    
    code: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True)
    role: str = Field(default="user") # "admin", "user"
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = 1 # None = unlimited
    used_count: int = Field(default=0)
    is_revoked: bool = Field(default=False)
    
    created_by_id: Optional[int] = Field(default=None, sa_column=Column(Integer, ForeignKey("users.id", ondelete="SET NULL")))
    
    created_by: Optional["User"] = Relationship(
        back_populates="created_invitations",
        sa_relationship_kwargs={"foreign_keys": "Invitation.created_by_id"}
    )
    
    users: List["User"] = Relationship(
        back_populates="invitation",
        sa_relationship_kwargs={"foreign_keys": "User.invitation_id"}
    )
