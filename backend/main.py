from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# Apply HuggingFace patch for Pyannote compatibility
import backend.utils.hf_patch
from sqlmodel import SQLModel
from backend.core.db import sync_engine
from backend.api.v1.api import api_router

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
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
