import asyncio
import logging
from typing import Any, Optional

from celery.result import AsyncResult
from docker.client import DockerClient
from docker.errors import DockerException, NotFound
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

import docker
from backend.api.deps import (
    get_current_active_superuser,
    get_current_active_superuser_ws,
    get_current_admin_user,
    get_current_user,
    get_db,
)
from backend.api.services.health_service import (
    get_admin_health_status,
    get_system_health_status,
)
from backend.api.v1.endpoints.setup import (
    FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL,
    is_system_initialized,
    require_first_run_password,
)
from backend.core.security import (
    MIN_PASSWORD_LENGTH,
    hash_user_password,
    validate_password_policy,
)
from backend.models.user import User
from backend.preload_models import check_model_status
from backend.seed_demo import seed_demo_data
from backend.services.model_preparation import enqueue_model_preparation
from backend.utils.config_manager import config_manager, get_trusted_web_origin
from backend.utils.download_progress import (
    get_download_progress,
    is_download_in_progress,
)

router = APIRouter()
logger = logging.getLogger(__name__)

RETIRED_COMPANION_RESPONSE = {
    "error": "companion_retired",
    "message": "The Nojoin Companion app has been retired. Please update your installation and use the web app for recording.",
    "see": "https://github.com/Valtora/Nojoin/blob/main/docs/CAPTURE.md",
}


def resolve_tls_fingerprint() -> str | None:
    import hashlib
    import os
    import socket
    import ssl
    from urllib.parse import urlparse

    def format_fingerprint(certificate_bytes: bytes) -> str:
        fingerprint = hashlib.sha256(certificate_bytes).hexdigest().upper()
        return ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2))

    trusted_origin = get_trusted_web_origin()
    parsed_origin = urlparse(trusted_origin)
    hostname = parsed_origin.hostname
    port = parsed_origin.port or 443

    if parsed_origin.scheme != "https":
        logger.info("Trusted web origin is not HTTPS; skipping TLS fingerprint lookup.")
        return None

    if hostname and hostname not in ["127.0.0.1", "localhost", "backend", "api", "::1"]:
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.minimum_version = ssl.TLSVersion.TLSv1_2

            with socket.create_connection((hostname, port), timeout=3.0) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert_der = ssock.getpeercert(binary_form=True)
                    if cert_der:
                        formatted_fp = format_fingerprint(cert_der)
                        logger.info(
                            "Retrieved TLS fingerprint from trusted HTTPS origin."
                        )
                        return formatted_fp
        except Exception:  # noqa: BLE001
            logger.warning(
                "Dynamic TLS certificate retrieval failed; falling back to local certificate."
            )

    cert_paths = [
        "/etc/nginx/certs/cert.crt",
        "/app/nginx/certs/cert.crt",
        "/app/nginx/cert.crt",
        "nginx/cert.crt",
    ]

    cert_path = None
    for path in cert_paths:
        if os.path.exists(path):
            cert_path = path
            break

    if not cert_path:
        logger.error("No local certificate file found for fallback.")
        return None

    try:
        with open(cert_path, "r", encoding="utf-8") as f:
            pem_data = f.read()

        der_data = ssl.PEM_cert_to_DER_cert(pem_data)
        formatted_fp = format_fingerprint(der_data)
        logger.info("Retrieved TLS fingerprint from local certificate fallback.")
        return formatted_fp
    except Exception:  # noqa: BLE001
        logger.error(
            "Unable to resolve TLS fingerprint from local certificate fallback."
        )
        return None


# Initialize Docker client
client: DockerClient | None = None
client_init_error: str | None = None
try:
    client = docker.from_env()
except DockerException as e:
    logger.error(f"Failed to initialize Docker client: {e}")
    client_init_error = str(e)


ALLOWED_CONTAINERS = {
    # Production container names
    "nojoin-api",
    "nojoin-worker",
    "nojoin-frontend",
    "nojoin-nginx",
    "nojoin-redis",
    "nojoin-db",
    "nojoin-socket-proxy",
    # Development container names
    "nojoin-dev-api",
    "nojoin-dev-worker",
    "nojoin-dev-frontend",
    "nojoin-dev-nginx",
    "nojoin-dev-redis",
    "nojoin-dev-db",
    "nojoin-dev-socket-proxy",
}


@router.get("/logs/download")
def download_logs(
    container: str, current_user: User = Depends(get_current_active_superuser)
):
    """
    Download logs for a specific container.
    Requires Superuser privileges.
    """
    if container not in ALLOWED_CONTAINERS:
        raise HTTPException(status_code=403, detail="Forbidden")

    docker_client = client
    if docker_client is None:
        raise HTTPException(status_code=503, detail="Docker client unavailable")

    try:
        container_obj = docker_client.containers.get(container)
        logs = container_obj.logs(timestamps=True).decode("utf-8")

        def iter_logs():
            yield logs

        return StreamingResponse(
            iter_logs(),
            media_type="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename={container}_logs.txt"
            },
        )
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container {container} not found")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching logs for {container}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/logs/live")
async def websocket_logs(
    websocket: WebSocket,
    container: str,
    user: User = Depends(get_current_active_superuser_ws),
):
    """
    WebSocket endpoint for streaming logs.
    Uses threaded workers to read Docker logs (which is blocking)
    and pushes to an asyncio queue to prevent blocking the main event loop.
    Supports streaming from a single container or "all" containers.
    """
    if container != "all" and container not in ALLOWED_CONTAINERS:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    docker_client = client
    if docker_client is None:
        error_msg = (
            f"Error: Docker client unavailable. {client_init_error}"
            if client_init_error
            else "Error: Docker client unavailable"
        )
        await websocket.send_text(error_msg)
        await websocket.close()
        return

    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    active = True

    def log_reader(
        container_name: str, q: asyncio.Queue, loop: asyncio.AbstractEventLoop
    ):
        """
        Thread target: reads logs from a container and puts them into the queue.
        """
        try:
            c = docker_client.containers.get(container_name)
            # logs(stream=True) blocks until new log arrives
            for line in c.logs(stream=True, tail=2000, follow=True, timestamps=True):
                if not active:
                    break
                text = line.decode("utf-8")
                # Format: [container-name] log info...
                formatted_line = f"[{container_name}] {text}"
                # Schedule put_nowait/put to run in the main loop
                asyncio.run_coroutine_threadsafe(q.put(formatted_line), loop)
        except Exception as e:  # noqa: BLE001
            if active:
                err_msg = f"[{container_name}] Error reading logs: {e}"
                asyncio.run_coroutine_threadsafe(q.put(err_msg), loop)

    # Determine which containers to stream
    targets = []
    if container == "all":
        # Get all Nojoin containers
        try:
            # Filter by name prefix or label if possible. For now, strict list.
            containers_list = [
                "nojoin-api",
                "nojoin-worker",
                "nojoin-frontend",
                "nojoin-nginx",
                "nojoin-redis",
                "nojoin-db",
            ]
            for c_name in containers_list:
                targets.append(c_name)
        except Exception as e:  # noqa: BLE001
            await websocket.send_text(f"Error listing containers: {e}")
            await websocket.close()
            return
    else:
        targets.append(container)

    # Start reader threads
    import threading

    threads = []
    for target in targets:
        t = threading.Thread(target=log_reader, args=(target, queue, loop), daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            # Wait for next line from any container
            line = await queue.get()
            await websocket.send_text(line)
    except WebSocketDisconnect:
        # Client disconnected
        pass
    except Exception as e:  # noqa: BLE001
        logger.error(f"WebSocket send error: {e}")
    finally:
        active = False
        # Daemon threads exit when the process stops. The 'active' flag
        # breaks the read loop on the next log line, since c.logs() blocks.
        try:
            await websocket.close()
        except:
            pass


class SetupRequest(BaseModel):
    username: str
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    whisper_model_size: Optional[str] = "turbo"
    selected_model: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value)


@router.get("/health")
async def get_system_health(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Return authenticated system health detail.
    """
    return await get_system_health_status()


@router.get("/admin-health")
async def get_admin_health(
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Return admin-only operational readiness state for the processing pipeline.
    """
    return await get_admin_health_status(db)


@router.get("/status")
async def get_system_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Check if the system is initialized (has at least one user).
    """
    return {"initialized": await is_system_initialized(db)}


@router.get("/check-ffmpeg")
async def check_ffmpeg(current_user: User = Depends(get_current_admin_user)) -> Any:
    """
    Check if ffmpeg is available in the system PATH.
    """
    import shutil

    from backend.utils.audio import ensure_ffmpeg_in_path

    # Try to ensure it's in path first
    ensure_ffmpeg_in_path()

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    return {
        "ffmpeg": ffmpeg_path is not None,
        "ffprobe": ffprobe_path is not None,
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
    }


@router.post("/setup")
async def setup_system(
    *,
    request: Request,
    db: AsyncSession = Depends(get_db),
    setup_in: SetupRequest,
) -> Any:
    """
    Initialize the system with the first admin user and initial configuration.
    Only works if no users exist.
    """
    if await is_system_initialized(db):
        raise HTTPException(
            status_code=403,
            detail=FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL,
        )

    require_first_run_password(request)

    # Construct settings dict
    settings = {"whisper_model_size": setup_in.whisper_model_size}

    llm_provider = config_manager.get("llm_provider", "gemini")
    if setup_in.selected_model:
        if llm_provider == "ollama":
            settings["ollama_model"] = setup_in.selected_model
        else:
            settings[f"{llm_provider}_model"] = setup_in.selected_model

    user = User(
        username=setup_in.username,
        hashed_password=hash_user_password(setup_in.password),
        is_superuser=True,
        force_password_change=False,  # First user sets their own password, so no need to force change
        role="owner",
        settings=settings,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Seed demo data for the new admin user
    if user.id is None:
        logger.error(
            "Failed to seed demo data during setup: created user has no persisted ID."
        )
    else:
        try:
            await seed_demo_data(user.id)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to seed demo data during setup: {e}")

    model_preparation_task_id = None
    try:
        model_preparation_task_id = enqueue_model_preparation(
            whisper_model_size=setup_in.whisper_model_size or "turbo",
            transcription_backend="whisper",
            include_core=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Failed to queue first-run model preparation: %s", e, exc_info=True
        )

    return {"initialized": True, "model_preparation_task_id": model_preparation_task_id}


@router.get("/download-progress")
async def get_download_progress_endpoint(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get current automatic model preparation progress.
    """
    progress = get_download_progress()
    if progress is None:
        return {
            "progress": 0,
            "message": "No model preparation in progress",
            "speed": None,
            "eta": None,
            "status": "idle",
            "stage": None,
            "in_progress": False,
        }
    progress["in_progress"] = is_download_in_progress()
    return progress


@router.get("/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get the status of a background task.
    """
    from backend.models.task import check_task_ownership

    if not await check_task_ownership(db, task_id, current_user):
        raise HTTPException(status_code=404, detail="Task not found")

    task_result = AsyncResult(task_id)

    # Handle potential serialization errors if result contains exceptions
    result_data = None
    if task_result.status == "FAILURE":
        result_data = "Task failed. Check server logs for details."
    elif task_result.status == "SUCCESS":
        result_data = task_result.result
    else:
        # For PENDING, STARTED, RETRY etc.
        # If it's an exception object, convert to string
        if isinstance(task_result.result, Exception):
            result_data = str(task_result.result)
        else:
            result_data = task_result.result

    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": result_data,
    }

    # If the task is in a custom state (like PROCESSING), result might be the meta dict
    # Celery stores meta info in .info for custom states
    if task_result.status == "PROCESSING":
        # Ensure we're accessing a dict
        info = task_result.info
        if isinstance(info, dict):
            response["progress"] = info.get("progress", 0)
            response["message"] = info.get("message", "")
            # Pass through other meta fields like speed/eta if present
            response["result"] = info
        else:
            response["message"] = str(info)

    return response


@router.get("/models/status")
async def get_models_status(
    whisper_model_size: Optional[str] = None,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get the status of all models.
    """
    status = check_model_status(whisper_model_size=whisper_model_size)
    is_admin = current_user.role in ["owner", "admin"] or current_user.is_superuser
    if not is_admin:
        for model in status:
            status[model]["path"] = None
            status[model]["checked_paths"] = []
    return status


@router.delete("/models/{model_name}")
async def delete_model_endpoint(
    model_name: str,
    variant: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Delete a specific model from the cache.
    """
    from backend.preload_models import delete_model

    if model_name not in ["whisper", "pyannote", "embedding", "parakeet", "canary"]:
        raise HTTPException(status_code=400, detail="Invalid model name")

    try:
        success = delete_model(model_name, whisper_model_size=variant)
        if success:
            return {"message": f"Model {model_name} deleted successfully"}
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Model {model_name} not found or could not be deleted",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seed-demo")
async def seed_demo(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Re-create the demo meeting for the current user if it doesn't exist.
    """
    if current_user.id:
        await seed_demo_data(user_id=current_user.id, force=True)
    return {"message": "Demo data seeding initiated"}


@router.get("/demo-recording")
async def get_demo_recording(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get the demo recording for the current user.
    Returns the recording ID if it exists, otherwise returns None.
    """
    from sqlmodel import select

    from backend.models.recording import Recording

    query = select(Recording).where(
        Recording.name == "Welcome to Nojoin", Recording.user_id == current_user.id
    )
    result = await db.execute(query)
    recording = result.scalar_one_or_none()

    if recording:
        return {"id": recording.public_id}
    return {"id": None}


@router.get("/companion-releases")
async def get_companion_releases(
    current_user: User = Depends(get_current_user),
) -> Any:
    return JSONResponse(status_code=410, content=RETIRED_COMPANION_RESPONSE)


@router.get("/fingerprint")
async def get_tls_fingerprint(current_user: User = Depends(get_current_user)) -> Any:
    """
    Get the SHA-256 fingerprint of the TLS certificate.
    Attempts to fetch the certificate dynamically from the public-facing hostname.
    Falls back to hashing the local self-signed certificate.
    """
    return {"fingerprint": resolve_tls_fingerprint()}
