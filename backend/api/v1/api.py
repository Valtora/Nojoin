from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from backend.api.deps import get_current_user
from backend.api.v1.endpoints import api_docs, backup, calendar, documents, invitations, llm, login, people_tags, recordings, settings, setup, speakers, system, tags, tasks, transcripts, users, version

api_router = APIRouter()
legacy_companion_router = APIRouter()

COMPANION_RETIRED_RESPONSE = {
    "error": "companion_retired",
    "message": "The Nojoin Companion app has been retired. Please update your installation and use the web app for recording.",
    "see": "https://github.com/Valtora/Nojoin/blob/main/docs/CAPTURE.md",
}
LEGACY_COMPANION_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _companion_retired() -> JSONResponse:
    return JSONResponse(status_code=410, content=COMPANION_RETIRED_RESPONSE)


@legacy_companion_router.api_route(
    "/companion/{legacy_path:path}",
    methods=LEGACY_COMPANION_METHODS,
    include_in_schema=False,
)
async def retired_companion_prefix(legacy_path: str) -> JSONResponse:
    return _companion_retired()


@legacy_companion_router.api_route(
    "/login/companion-local-token",
    methods=["GET", "POST"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing",
    methods=["POST", "DELETE"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing/pending",
    methods=["DELETE"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing/disconnect",
    methods=["POST"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing/requests/{request_id}",
    methods=["GET", "DELETE"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing/request/opened",
    methods=["POST"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing/request/reject",
    methods=["POST"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-pairing/request/complete",
    methods=["POST"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-events",
    methods=["GET"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/login/companion-token/exchange",
    methods=["POST"],
    include_in_schema=False,
)
@legacy_companion_router.api_route(
    "/recordings/{recording_id}/upload-token",
    methods=["POST"],
    include_in_schema=False,
)
async def retired_companion_routes(**_: str) -> JSONResponse:
    return _companion_retired()

api_router.include_router(api_docs.router, tags=["docs"])
api_router.include_router(login.router, tags=["login"])
api_router.include_router(legacy_companion_router)
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
    tags=["recordings"]
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
    tasks.router,
    prefix="/tasks",
    tags=["tasks"],
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
api_router.include_router(
    calendar.router,
    prefix="/calendar",
    tags=["calendar"],
)
