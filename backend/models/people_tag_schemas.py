from typing import Optional

from sqlmodel import SQLModel

from .base import BaseDBModel
from .int_bounds import RowId


class PeopleTagCreate(SQLModel):
    name: str
    color: Optional[str] = None
    parent_id: Optional[RowId] = None


class PeopleTagUpdate(SQLModel):
    name: Optional[str] = None
    color: Optional[str] = None
    parent_id: Optional[RowId] = None


class PeopleTagRead(BaseDBModel):
    id: int
    name: str
    color: Optional[str] = None
    parent_id: Optional[int] = None
