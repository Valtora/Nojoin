from typing import Optional
from sqlmodel import SQLModel
from .base import BaseDBModel

class PeopleTagCreate(SQLModel):
    name: str
    color: Optional[str] = None
    parent_id: Optional[int] = None

class PeopleTagUpdate(SQLModel):
    name: Optional[str] = None
    color: Optional[str] = None
    parent_id: Optional[int] = None

class PeopleTagRead(BaseDBModel):
    id: int
    name: str
    color: Optional[str] = None
    parent_id: Optional[int] = None
