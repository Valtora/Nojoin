from typing import AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from backend.core.db import get_session

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session
