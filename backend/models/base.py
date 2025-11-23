from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
import sqlalchemy as sa

class TimestampMixin(SQLModel):
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_type=sa.DateTime,
        nullable=False
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_type=sa.DateTime,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.utcnow}
    )

class BaseDBModel(TimestampMixin):
    id: Optional[int] = Field(default=None, primary_key=True)
