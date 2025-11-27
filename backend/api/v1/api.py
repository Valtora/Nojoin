from fastapi import APIRouter, Depends
from backend.api.deps import get_current_user
from backend.api.v1.endpoints import recordings, speakers, tags, settings, login, transcripts, users, system

api_router = APIRouter()

api_router.include_router(login.router, tags=["login"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    recordings.router, 
    prefix="/recordings", 
    tags=["recordings"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    transcripts.router,
    prefix="/transcripts",
    tags=["transcripts"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    speakers.router, 
    prefix="/speakers", 
    tags=["speakers"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    tags.router, 
    prefix="/tags", 
    tags=["tags"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    settings.router, 
    prefix="/settings", 
    tags=["settings"],
    dependencies=[Depends(get_current_user)]
)


