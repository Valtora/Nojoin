from typing import Optional
from sqlmodel import Field, Relationship
from sqlalchemy import BigInteger
from backend.models.base import BaseDBModel
from backend.models.user import User
from backend.models.recording import Recording

class ChatMessage(BaseDBModel, table=True):
    __tablename__ = "chat_messages"
    
    recording_id: int = Field(foreign_key="recordings.id", index=True, sa_type=BigInteger)
    user_id: int = Field(foreign_key="users.id", index=True, sa_type=BigInteger)
    role: str = Field(description="Role of the message sender: 'user' or 'assistant'")
    content: str = Field(description="Content of the chat message")

    # Relationships
    # user: Optional["User"] = Relationship(back_populates="chat_messages")
    recording: Optional["Recording"] = Relationship(back_populates="chat_messages")

