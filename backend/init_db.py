import asyncio
import logging
from sqlmodel import SQLModel
from backend.core.db import engine
# Import all models to ensure they are registered
from backend.models.base import BaseDBModel
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.chat import ChatMessage
from backend.seed_demo import seed_demo_data

logger = logging.getLogger(__name__)

async def init_db():
    logger.info("Initialising database...")
    async with engine.begin() as conn:
        # await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    
    # Seed demo data
    try:
        await seed_demo_data()
    except Exception as e:
        logger.error(f"Failed to seed demo data: {e}")
        
    logger.info("Database initialised successfully.")

if __name__ == "__main__":
    asyncio.run(init_db())