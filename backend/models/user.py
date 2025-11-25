from typing import Optional
from sqlmodel import Field, SQLModel
from backend.models.base import BaseDBModel

class User(BaseDBModel, table=True):
    __tablename__ = "users"
    username: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None, index=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
