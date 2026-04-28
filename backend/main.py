from contextlib import asynccontextmanager
from ipaddress import ip_address
import logging
from urllib.parse import urlparse

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.core.audio_setup import setup_audio_environment
from sqlmodel import select
from backend.api.v1.api import api_router
from backend.utils.version import get_installed_version
from backend.startup_migrations import run_startup_migrations, should_skip_startup_migrations

setup_audio_environment()

logger = logging.getLogger(__name__)

SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}


def _normalise_forwarded_header(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(",", 1)[0].strip().lower() or None


def _normalise_forwarded_host(value: str | None) -> str | None:
    host = _normalise_forwarded_header(value)
    if not host:
        return None

    parsed = urlparse(f"//{host}")
    return parsed.hostname.lower() if parsed.hostname else None


def _is_private_client_address(host: str | None) -> bool:
    if not host:
        return False

    try:
        client_ip = ip_address(host)
    except ValueError:
        return host in {"localhost", "testclient"}

    return (
        client_ip.is_private
        or client_ip.is_loopback
        or client_ip.is_link_local
        or client_ip.is_reserved
    )


class EnforceCanonicalHttpsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if self._is_request_https(request):
            return await call_next(request)

        if request.method in SAFE_HTTP_METHODS:
            return RedirectResponse(
                url=self._build_redirect_url(request),
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            )

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Plain HTTP requests are not allowed. Use HTTPS."},
        )

    def _is_request_https(self, request: Request) -> bool:
        if request.url.scheme == "https":
            return True

        if not _is_private_client_address(request.client.host if request.client else None):
            return False

        forwarded_proto = _normalise_forwarded_header(request.headers.get("x-forwarded-proto"))
        if forwarded_proto != "https":
            return False

        forwarded_host = _normalise_forwarded_host(request.headers.get("x-forwarded-host"))
        host = _normalise_forwarded_host(request.headers.get("host"))
        canonical_host = urlparse(get_trusted_web_origin()).hostname

        return bool(
            canonical_host
            and host == canonical_host
            and forwarded_host == canonical_host
        )

    def _build_redirect_url(self, request: Request) -> str:
        canonical_origin = get_trusted_web_origin().rstrip("/")
        redirect_target = f"{canonical_origin}{request.url.path}"
        if request.url.query:
            redirect_target = f"{redirect_target}?{request.url.query}"
        return redirect_target

# Import models to register them with SQLModel
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.models.chat import ChatMessage
from backend.models.companion_pairing import CompanionPairing
from backend.models.task import UserTask
from backend.models.calendar import CalendarProviderConfig, CalendarConnection, CalendarSource, CalendarEvent
from backend.core.db import async_session_maker
from backend.seed_demo import seed_demo_data
from backend.services.recording_identity_service import ensure_recording_meeting_uids
from backend.utils.config_manager import (
    get_cors_origin_list,
    get_trusted_host_list,
    get_trusted_web_origin,
)

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
    if should_skip_startup_migrations():
        logger.info("Skipping app-startup migrations because they already ran before process start.")
        return

    run_startup_migrations()

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
        version=get_installed_version(),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=app_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origin_list(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Range"],
        expose_headers=["Accept-Ranges", "Content-Disposition", "Content-Length", "Content-Range"],
    )

    app.add_middleware(EnforceCanonicalHttpsMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_trusted_host_list())

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
