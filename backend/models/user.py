from typing import Optional, Dict, Any, List, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from backend.models.base import BaseDBModel
from enum import Enum
from datetime import datetime

if TYPE_CHECKING:
    from backend.models.invitation import Invitation

class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"

class User(BaseDBModel, table=True):
    __tablename__ = "users"
    username: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None, index=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    force_password_change: bool = Field(default=False)
    role: str = Field(default=UserRole.USER)
    settings: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    invitation_id: Optional[int] = Field(default=None, foreign_key="invitations.id")
    
    invitation: Optional["Invitation"] = Relationship(
        back_populates="users",
        sa_relationship_kwargs={"foreign_keys": "User.invitation_id"}
    )
    
    created_invitations: List["Invitation"] = Relationship(
        back_populates="created_by",
        sa_relationship_kwargs={"foreign_keys": "Invitation.created_by_id"}
    )

class UserCreate(SQLModel):
    username: str
    password: str
    email: Optional[str] = None
    is_superuser: bool = False
    role: str = UserRole.USER
    invite_code: Optional[str] = None

class UserRead(SQLModel):
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    is_superuser: bool
    force_password_change: bool
    role: str
    created_at: datetime
    updated_at: datetime

class UserList(SQLModel):
    items: List[UserRead]
    total: int

class UserUpdate(SQLModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    role: Optional[str] = None

class UserPasswordUpdate(SQLModel):
    current_password: str
    new_password: str
