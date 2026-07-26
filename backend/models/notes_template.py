from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Text
from sqlmodel import Field, SQLModel

from .base import BaseDBModel


class NotesTemplateScope(str, Enum):
    """Who a template belongs to.

    ``INSTALL`` templates are managed by the owner/admins, visible to every user
    and read-only for regular users. ``PERSONAL`` templates belong to the user in
    ``user_id`` and are invisible to everyone else.
    """

    INSTALL = "install"
    PERSONAL = "personal"


class NotesTemplate(BaseDBModel, table=True):
    __tablename__ = "notes_templates"

    name: str = Field(index=True)
    # One line on what the structure is for, shown beside it in the picker and
    # the settings list so a library of several is navigable.
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    sections: str = Field(sa_column=Column(Text, nullable=False))
    scope: str = Field(default=NotesTemplateScope.PERSONAL.value, index=True)

    # NULL for install templates. Personal templates are deleted with their user.
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
    )

    # Which version of the shipped DEFAULT_NOTES_SECTIONS this template was
    # forked from, or NULL when it was written from scratch. A template with a
    # version older than NOTES_SECTIONS_VERSION is stale: the shipped structure
    # has improved since the fork, and the UI offers a diff and a reset. Written
    # from scratch means there is nothing to be stale against.
    builtin_version: Optional[int] = Field(default=None)


class NotesTemplateCreate(SQLModel):
    name: str
    description: Optional[str] = None
    sections: str
    scope: Optional[str] = None
    builtin_version: Optional[int] = None


class NotesTemplateUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sections: Optional[str] = None


class NotesTemplateRead(BaseDBModel):
    name: str
    description: Optional[str] = None
    sections: str
    scope: str
    user_id: Optional[int] = None
    builtin_version: Optional[int] = None
    # Computed for the UI rather than stored: see resolve_template_flags.
    is_editable: bool = False
    is_stale: bool = False
    is_install_default: bool = False
    is_user_default: bool = False
