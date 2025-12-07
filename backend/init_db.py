import asyncio
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

async def init_db():
    print("Initializing database...")
    async with engine.begin() as conn:
        # await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    
    # Seed demo data
    try:
        await seed_demo_data()
    except Exception as e:
        print(f"Failed to seed demo data: {e}")
        
    print("Database initialized successfully!")

if __name__ == "__main__":
    asyncio.run(init_db())