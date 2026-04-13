from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import time
import logging
from sqlalchemy.exc import OperationalError
from backend.core.audio_setup import setup_audio_environment
from sqlmodel import SQLModel, Session, text, select
from backend.core.db import sync_engine
from backend.api.v1.api import api_router
from alembic.config import Config
from alembic import command
from backend.api.services.health_service import APP_HEALTH_VERSION

setup_audio_environment()

logger = logging.getLogger(__name__)

# Import models to register them with SQLModel
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.models.chat import ChatMessage
from backend.models.task import UserTask
from backend.models.calendar import CalendarProviderConfig, CalendarConnection, CalendarSource, CalendarEvent
from backend.core.db import async_session_maker
from backend.seed_demo import seed_demo_data
from backend.services.recording_identity_service import ensure_recording_meeting_uids

async def ensure_owner_exists():
    """
    Ensures that at least one user has the OWNER role.
    If no owner exists, it tries to create/promote a default owner from env custom vars,
    defaulting to 'admin' / 'changeme123'.
    """
    async with async_session_maker() as session:
        # Check if any owner exists
        query = select(User).where(User.role == "owner")
        result = await session.execute(query)
        owner = result.scalar_one_or_none()
        
        if not owner:
            logger.warning("No owner found. Promoting the first user to OWNER.")
            # If no owner, promote the first user
            query = select(User).order_by(User.id).limit(1)
            result = await session.execute(query)
            first_user = result.scalar_one_or_none()
            
            if first_user:
                logger.info(f"Promoting user {first_user.username} (ID: {first_user.id}) to OWNER")
                first_user.role = "owner"
                session.add(first_user)
                await session.commit()
            else:
                logger.warning("No users found to promote.")


async def ensure_recording_meeting_uids_on_startup() -> None:
    async with async_session_maker() as session:
        repaired_count = await ensure_recording_meeting_uids(session)

    if repaired_count:
        logger.warning(
            "Backfilled meeting_uid for %s existing recording(s) during startup.",
            repaired_count,
        )


def run_migrations():
    # Wait for DB to be ready
    max_retries = 30
    retry_interval = 1
    
    logger.info("Waiting for database connection...")
    for i in range(max_retries):
        try:
            with sync_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection established.")
            break
        except OperationalError:
            if i == max_retries - 1:
                logger.error("Could not connect to database after multiple retries.")
                raise
            logger.info(f"Database not ready, retrying in {retry_interval}s...")
            time.sleep(retry_interval)

    try:
        import subprocess
        import sys
        # Use the same python interpreter to run alembic module
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    except Exception as e:
        logger.error(f"Error running migrations: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    await ensure_owner_exists()
    await ensure_recording_meeting_uids_on_startup()
    # Seed demo data for the initial user if needed
    try:
        await seed_demo_data()
    except Exception as e:
        logger.error(f"Failed to seed demo data on startup: {e}")
    yield

def create_app(*, app_lifespan=lifespan) -> FastAPI:
    app = FastAPI(
        title="Nojoin API",
        description="Backend API for Nojoin - Containerized Meeting Intelligence",
        version=APP_HEALTH_VERSION,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=app_lifespan,
    )

    origins = [
        "http://localhost:14141",
        "http://localhost:3000",
        "http://127.0.0.1:14141",
        "https://localhost",
        "https://localhost:14141",
        "https://localhost:14443",
    ]

    env_origins = os.getenv("ALLOWED_ORIGINS", "")
    if env_origins:
        origins.extend([origin.strip() for origin in env_origins.split(",")])

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Range"],
        expose_headers=["Accept-Ranges", "Content-Disposition", "Content-Length", "Content-Range"],
    )

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health")
    @app.get("/api/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
