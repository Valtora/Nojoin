from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
import sqlalchemy as sa
from sqlalchemy import BigInteger

from backend.utils.time import utc_now

class TimestampMixin(SQLModel):
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_type=sa.DateTime,
        nullable=False
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_type=sa.DateTime,
        nullable=False,
        sa_column_kwargs={"onupdate": utc_now}
    )

class BaseDBModel(TimestampMixin):
    id: Optional[int] = Field(default=None, primary_key=True, sa_type=BigInteger)
