from fastapi import APIRouter, Depends
from backend.api.deps import get_current_user
from backend.api.v1.endpoints import recordings, speakers, tags, settings, login, transcripts, users, system, setup, llm, backup, invitations, documents, version, people_tags

api_router = APIRouter()

api_router.include_router(login.router, tags=["login"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(setup.router, prefix="/setup", tags=["setup"])
api_router.include_router(
    llm.router,
    prefix="/llm",
    tags=["llm"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["users"],
)
api_router.include_router(
    invitations.router,
    prefix="/invitations",
    tags=["invitations"],
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
    people_tags.router, 
    prefix="/people-tags", 
    tags=["people-tags"],
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
api_router.include_router(
    settings.router,
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    version.router,
    prefix="/version",
    tags=["version"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    backup.router,
    prefix="/backup",
    tags=["backup"],
    dependencies=[Depends(get_current_user)]
)
api_router.include_router(
    documents.router,
    tags=["documents"],
    dependencies=[Depends(get_current_user)]
)
