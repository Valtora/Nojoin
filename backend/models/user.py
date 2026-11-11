from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import computed_field, field_validator
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from backend.core.security import MIN_PASSWORD_LENGTH, validate_password_policy
from backend.models.base import BaseDBModel
from backend.models.int_bounds import INTEGER_MAX

if TYPE_CHECKING:
    from backend.models.invitation import Invitation


class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"


class User(BaseDBModel, table=True):
    __tablename__ = "users"
    username: str = Field(index=True, unique=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    force_password_change: bool = Field(default=False)
    role: str = Field(default=UserRole.USER)
    token_version: int = Field(default=0, nullable=False)
    settings: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    has_seen_demo_recording: bool = Field(default=False)
    # Per-user cap on AI analysis runs per UTC day, set by an administrator.
    # Null inherits the install's ai_analysis_daily_limit; 0 means no limit.
    ai_analysis_daily_limit: Optional[int] = Field(default=None, nullable=True)
    # When this user accepted the legal disclaimer (#1004): stamped once, from
    # the server's clock, by setup for the owner and by the acceptance route
    # for everyone else, and never changed after. Null until then, and for the
    # owner of an install set up before the time was recorded. The schema's one
    # timezone-aware column, so the record names its instant on its own.
    legal_accepted_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )

    invitation_id: Optional[int] = Field(default=None, foreign_key="invitations.id")

    invitation: Optional["Invitation"] = Relationship(
        back_populates="users",
        sa_relationship_kwargs={"foreign_keys": "User.invitation_id"},
    )

    created_invitations: List["Invitation"] = Relationship(
        back_populates="created_by",
        sa_relationship_kwargs={"foreign_keys": "Invitation.created_by_id"},
    )


def needs_legal_acceptance(
    *, is_superuser: bool, legal_accepted_at: Optional[datetime]
) -> bool:
    """Whether a user must accept the legal disclaimer before recording (#1004).

    Everyone must, once, except the setup account: the account first-run setup
    creates, which ADMIN.md tells apart by its superuser rights. Its owner
    accepts on the wizard's Legal step, so on an install set up before
    acceptance times were recorded it has no time and is not asked again. The
    web app cannot grant superuser rights; a superuser who grants them through
    the API vouches for that account as for their own.
    """
    return legal_accepted_at is None and not is_superuser


class UserCreate(SQLModel):
    username: str
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    is_superuser: bool = False
    role: str = UserRole.USER
    invite_code: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value)


class UserRead(SQLModel):
    id: int
    username: str
    is_active: bool
    is_superuser: bool
    force_password_change: bool
    role: str
    ai_analysis_daily_limit: Optional[int] = None
    legal_accepted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("legal_accepted_at")
    @classmethod
    def legal_accepted_at_in_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        # SQLite, which the tests use, returns the stored instant without its
        # zone. The column holds UTC, so the API always says so.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @computed_field  # type: ignore[prop-decorator]  # mypy doesn't support @computed_field over @property
    @property
    def legal_acceptance_required(self) -> bool:
        return needs_legal_acceptance(
            is_superuser=self.is_superuser, legal_accepted_at=self.legal_accepted_at
        )


class UserList(SQLModel):
    items: List[UserRead]
    total: int


class UserUpdate(SQLModel):
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=MIN_PASSWORD_LENGTH)
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    role: Optional[str] = None
    # Applied by the administrator endpoint only; the self-service update
    # reads the username alone, so a user cannot raise their own cap.
    ai_analysis_daily_limit: Optional[int] = Field(default=None, ge=0, le=INTEGER_MAX)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_password_policy(value)


class UserPasswordUpdate(SQLModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_policy(value)
