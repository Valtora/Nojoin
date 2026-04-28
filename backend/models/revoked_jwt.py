from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from backend.utils.time import utc_now


class RevokedJwt(SQLModel, table=True):
    __tablename__ = "revoked_jwts"

    jti: str = Field(primary_key=True, max_length=64)
    user_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    token_type: str = Field(max_length=32, nullable=False)
    expires_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(), nullable=False, index=True)
    )
    revoked_at: datetime = Field(
        default_factory=utc_now,
        sa_column=sa.Column(sa.DateTime(), nullable=False),
    )
    reason: Optional[str] = Field(default=None, max_length=64)
