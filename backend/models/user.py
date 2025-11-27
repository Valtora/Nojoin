from typing import Optional, Dict, Any
from sqlmodel import Field, SQLModel
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from backend.models.base import BaseDBModel

class User(BaseDBModel, table=True):
    __tablename__ = "users"
    username: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None, index=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    force_password_change: bool = Field(default=False)
    settings: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))

class UserCreate(SQLModel):
    username: str
    password: str
    email: Optional[str] = None
    is_superuser: bool = False

class UserRead(SQLModel):
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    is_superuser: bool
    force_password_change: bool
    settings: Optional[Dict[str, Any]] = {}

class UserUpdate(SQLModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None

class UserPasswordUpdate(SQLModel):
    current_password: str
    new_password: str
