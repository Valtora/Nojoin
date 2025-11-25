from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from backend.core.audio_setup import setup_audio_environment
from sqlmodel import SQLModel, Session, text
from backend.core.db import sync_engine
from backend.api.v1.api import api_router
from backend.celery_app import celery_app

# Setup audio environment (patches torchaudio)
setup_audio_environment()

# Import models to register them with SQLModel
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.user import User

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    SQLModel.metadata.create_all(sync_engine)
    
    # Create default user (admin/admin) if not exists
    from backend.create_first_user import create_first_user
    try:
        await create_first_user()
    except Exception as e:
        print(f"Error creating first user: {e}")
        
    yield

app = FastAPI(
    title="Nojoin API",
    description="Backend API for Nojoin - Containerized Meeting Intelligence",
    version="2.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    lifespan=lifespan
)

# Configure CORS
# In production, replace allow_origins=["*"] with specific frontend domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    health_status = {
        "status": "ok",
        "version": "2.0.0",
        "components": {
            "db": "unknown",
            "worker": "unknown"
        }
    }

    # Check Database
    try:
        with Session(sync_engine) as session:
            session.exec(text("SELECT 1"))
        health_status["components"]["db"] = "connected"
    except Exception:
        health_status["components"]["db"] = "disconnected"
        health_status["status"] = "error"

    # Check Worker
    try:
        # inspect().ping() returns a dict of nodes { 'celery@hostname': {'ok': 'pong'} } or None
        inspector = celery_app.control.inspect()
        # Set a short timeout so we don't block the health check too long
        active_workers = inspector.ping()
        
        if active_workers:
            health_status["components"]["worker"] = "active"
        else:
            health_status["components"]["worker"] = "inactive"
            if health_status["status"] == "ok":
                health_status["status"] = "warning"
    except Exception:
        health_status["components"]["worker"] = "error"
        if health_status["status"] == "ok":
            health_status["status"] = "warning"

    return health_status

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
