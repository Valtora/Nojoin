from fastapi import APIRouter
from backend.api.v1.endpoints import recordings, speakers, tags, settings

api_router = APIRouter()

api_router.include_router(recordings.router, prefix="/recordings", tags=["recordings"])
api_router.include_router(speakers.router, prefix="/speakers", tags=["speakers"])
api_router.include_router(tags.router, prefix="/tags", tags=["tags"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
