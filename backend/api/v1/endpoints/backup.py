import logging
import os
import shutil
import uuid
from datetime import datetime

import redis.asyncio as redis
from celery.result import AsyncResult
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_active_superuser, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.celery_app import celery_app
from backend.core.backup_manager import (
    ARCHIVE_QUALITIES,
    ARCHIVE_QUALITY_COMPRESSED,
    RESTORE_LOCK_KEY,
    RESTORE_LOCK_TTL_SECONDS,
)
from backend.core.redis import REDIS_URL
from backend.models.user import User
from backend.utils.path_manager import PathManager
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.upload_limit import UPLOAD_LIMIT_BACKUP, stream_and_validate_upload

logger = logging.getLogger(__name__)

router = APIRouter()


async def _acquire_restore_lock(job_id: str) -> bool:
    """Claim the install-wide restore lock, or report that one is already running.

    Fails open when Redis is unreachable: refusing every restore because the lock store
    is down would be worse than the race it guards against.
    """
    try:
        client = redis.from_url(REDIS_URL, decode_responses=True)
        acquired = await client.set(
            RESTORE_LOCK_KEY, job_id, nx=True, ex=RESTORE_LOCK_TTL_SECONDS
        )
        await client.aclose()
        return bool(acquired)
    except Exception as e:  # noqa: BLE001
        logger.warning("Restore lock unavailable, proceeding without it: %s", e)
        return True


async def _release_restore_lock() -> None:
    try:
        client = redis.from_url(REDIS_URL, decode_responses=True)
        await client.delete(RESTORE_LOCK_KEY)
        await client.aclose()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to release restore lock: %s", e)


async def _dispatch_restore(
    *,
    db: AsyncSession,
    current_user: User,
    archive_path,
    clear_existing: bool,
    overwrite_existing: bool,
) -> JSONResponse:
    """Queue a restore, refusing if one is already in flight."""
    from backend.models.task import register_task_ownership

    if not await _acquire_restore_lock("pending"):
        try:
            if archive_path.exists():
                os.unlink(archive_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=409,
            detail=(
                "A restore is already running on this server. Wait for it to finish "
                "before starting another."
            ),
        )

    try:
        task = celery_app.send_task(
            "backend.worker.tasks.restore_backup_task",
            kwargs={
                "zip_path": str(archive_path),
                "clear_existing": clear_existing,
                "overwrite_existing": overwrite_existing,
            },
        )
    except Exception:
        await _release_restore_lock()
        raise

    await register_task_ownership(db, task.id, current_user.id)

    return JSONResponse(
        status_code=202,
        content={"job_id": task.id, "message": "Restore started"},
    )


@router.post("/export")
async def export_backup(
    include_audio: bool = Query(True, description="Include audio files in backup"),
    archive_quality: str = Query(
        ARCHIVE_QUALITY_COMPRESSED,
        description=(
            "'compressed' re-encodes audio to Opus for size; 'original' stores it "
            "byte-for-byte so restored recordings can be reprocessed without loss."
        ),
    ),
    current_user: User = Depends(get_current_active_superuser),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger background backup creation.
    Returns: {"task_id": str}
    """
    if archive_quality not in ARCHIVE_QUALITIES:
        raise HTTPException(
            status_code=400,
            detail=f"archive_quality must be one of {', '.join(ARCHIVE_QUALITIES)}.",
        )

    try:
        # Trigger Celery task
        # Uses send_task to avoid importing the task function directly (bypasses heavy imports).
        task = celery_app.send_task(
            "backend.worker.tasks.create_backup_task",
            kwargs={
                "include_audio": include_audio,
                "archive_quality": archive_quality,
            },
        )
        from backend.models.task import register_task_ownership

        await register_task_ownership(db, task.id, current_user.id)
        return {"task_id": task.id}
    except Exception as e:  # noqa: BLE001
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to start backup export.",
            log_message="Failed to enqueue backup export task.",
            exc=e,
        )


@router.get("/export/{task_id}", dependencies=[Depends(get_current_active_superuser)])
async def get_export_status(task_id: str):
    """
    Get status of backup task.
    Returns: {"status": str, "result": dict | None}
    """
    try:
        task_result = AsyncResult(task_id, app=celery_app)

        if task_result.state == "PENDING":
            response = {"state": task_result.state, "status": "Pending..."}
        elif task_result.state != "FAILURE":
            response = {
                "state": task_result.state,
                "status": task_result.info.get("status", "")
                if isinstance(task_result.info, dict)
                else "",
                "result": task_result.result
                if task_result.state == "SUCCESS"
                else None,
            }
        else:
            # failure
            response = {
                "state": task_result.state,
                "status": "Backup export failed. Check server logs for details.",
            }

        return response
    except Exception as e:  # noqa: BLE001
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to load backup export status.",
            log_message=f"Failed to load backup export status for task '{task_id}'.",
            exc=e,
        )


@router.get(
    "/export/{task_id}/download", dependencies=[Depends(get_current_active_superuser)]
)
async def download_export(task_id: str, background_tasks: BackgroundTasks):
    """
    Download the result of a completed backup task.
    """
    try:
        task_result = AsyncResult(task_id, app=celery_app)

        if task_result.state != "SUCCESS":
            raise HTTPException(status_code=400, detail="Backup not ready or failed")

        result = task_result.result
        if not isinstance(result, dict) or "zip_path" not in result:
            raise HTTPException(status_code=500, detail="Invalid task result format")

        zip_path = result["zip_path"]
        if not os.path.exists(zip_path):
            raise HTTPException(
                status_code=404, detail="Backup file not found (triggered expired?)"
            )

        # Helper to clean up file after serving
        # Notes: We do NOT delete the file immediately here to support Resumable Downloads (Range requests).
        # The file will be cleaned up by the periodic cleanup task or subsequent operations.

        return FileResponse(
            path=zip_path,
            filename=f"nojoin_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            media_type="application/zip",
        )

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to retrieve the backup export.",
            log_message=f"Failed to download backup export for task '{task_id}'.",
            exc=e,
        )


@router.post("/import", dependencies=[Depends(get_current_active_superuser)])
async def import_backup(
    request: Request,
    file: UploadFile = File(...),
    clear_existing: bool = Query(
        False, description="Clear existing data before restoring"
    ),
    overwrite_existing: bool = Query(
        False, description="Overwrite existing recordings if they exist"
    ),
    current_user: User = Depends(get_current_active_superuser),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger background backup restoration.
    Returns: {"job_id": str, "message": str}
    """
    # Create persistent temporary connection for the file
    path_manager = PathManager()
    restore_temp_dir = path_manager.user_data_directory / "temp_restores"
    restore_temp_dir.mkdir(parents=True, exist_ok=True)

    upload_id = str(uuid.uuid4())
    # Sanitize filename to prevent path traversal. A multipart part without a filename
    # is a malformed request, not a server error.
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename supplied.")

    if ".." in file.filename or "/" in file.filename or "\\" in file.filename:
        logger.warning(f"Path traversal blocked for uploaded filename: {file.filename}")
        raise HTTPException(
            status_code=400,
            detail="Filename contains illegal path traversal characters.",
        )

    safe_filename = "".join(
        [c for c in file.filename if c.isalnum() or c in (" ", ".", "-", "_")]
    ).strip()
    if not safe_filename:
        safe_filename = "backup.zip"
    temp_path = restore_temp_dir / f"{upload_id}_{safe_filename}"

    async with enforce_upload_concurrency(
        request, "import_backup", str(current_user.id), 2
    ):
        try:
            await stream_and_validate_upload(
                file=file,
                dest_path=str(temp_path),
                max_size=UPLOAD_LIMIT_BACKUP,
            )

            return await _dispatch_restore(
                db=db,
                current_user=current_user,
                archive_path=temp_path,
                clear_existing=clear_existing,
                overwrite_existing=overwrite_existing,
            )

        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            # Cleanup on immediate failure
            try:
                if temp_path.exists():
                    os.unlink(temp_path)
            except OSError:
                pass
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to start backup restore.",
                log_message="Failed to start backup restore upload.",
                exc=e,
            )


@router.get("/import/{job_id}", dependencies=[Depends(get_current_active_superuser)])
async def get_import_status(job_id: str):
    """
    Get status of restore job.

    Reads the Celery result backend rather than an in-process dictionary, so the status
    survives an API restart and is the same whichever API worker answers.
    """
    task_result = AsyncResult(job_id, app=celery_app)
    state = task_result.state

    if state == "FAILURE":
        return {
            "status": "failed",
            "progress": "Failed",
            "error": "Restore failed. Check server logs for details.",
            "warnings": None,
        }

    if state == "SUCCESS":
        result = task_result.result if isinstance(task_result.result, dict) else {}
        return {
            "status": result.get("status", "completed"),
            "progress": result.get("progress", "Done"),
            "error": None,
            "warnings": result.get("warnings"),
        }

    if state == "PENDING":
        # Celery reports PENDING for an unknown id too, so this is also what a client
        # sees if it polls a job that never existed. It resolves either way.
        return {
            "status": "pending",
            "progress": "Queued",
            "error": None,
            "warnings": None,
        }

    info = task_result.info if isinstance(task_result.info, dict) else {}
    return {
        "status": "processing",
        "progress": info.get("progress", "Working..."),
        "error": None,
        "warnings": None,
    }


@router.post("/upload/init", dependencies=[Depends(get_current_active_superuser)])
async def init_upload(filename: str, file_size: int, total_chunks: int):
    """
    Initialize a multipart upload.
    Returns: {"upload_id": str}
    """
    # file_size was previously accepted and ignored, which left this route as an
    # unbounded write primitive: UPLOAD_LIMIT_BACKUP was only ever enforced on the
    # single-shot /import route that the UI never calls.
    if file_size < 0:
        raise HTTPException(status_code=400, detail="Invalid file size.")
    if file_size > UPLOAD_LIMIT_BACKUP:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Backup archive is too large: {file_size // (1024 * 1024)} MB exceeds "
                f"the {UPLOAD_LIMIT_BACKUP // (1024 * 1024)} MB limit. Raise "
                "UPLOAD_LIMIT_BACKUP if this archive is legitimate."
            ),
        )

    upload_id = str(uuid.uuid4())
    path_manager = PathManager()

    # Create temp directory (get_upload_temp_dir creates it as a side effect).
    path_manager.get_upload_temp_dir(upload_id)

    # Clean up old upload directories (older than 24h)
    temp_uploads_root = path_manager.user_data_directory / "temp_uploads"
    path_manager.cleanup_temp_files(temp_uploads_root, max_age_hours=24)

    return {"upload_id": upload_id}


@router.post(
    "/upload/{upload_id}/chunk", dependencies=[Depends(get_current_active_superuser)]
)
async def upload_chunk(upload_id: str, chunk_index: int, file: UploadFile = File(...)):
    """
    Upload a single chunk.
    """
    path_manager = PathManager()
    # get_chunk_path validates both request-derived components (upload_id via
    # UUID round-trip, chunk_index via int coercion) before returning a path
    # that is guaranteed to sit inside the upload dir.
    try:
        chunk_path = path_manager.get_chunk_path(upload_id, chunk_index)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid upload_id or chunk_index.")

    try:
        content = await file.read()

        # Enforce the limit against what has actually been written, not against what the
        # client declared at init. A declared size is a hint; this is the real bound.
        existing_bytes = sum(
            part.stat().st_size for part in chunk_path.parent.glob("*.part")
        )
        if existing_bytes + len(content) > UPLOAD_LIMIT_BACKUP:
            shutil.rmtree(chunk_path.parent, ignore_errors=True)
            raise HTTPException(
                status_code=413,
                detail=(
                    "Backup archive exceeds the "
                    f"{UPLOAD_LIMIT_BACKUP // (1024 * 1024)} MB upload limit."
                ),
            )

        with open(chunk_path, "wb") as f:
            f.write(content)
        return {"status": "ok", "chunk_index": chunk_index}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded chunk.",
            log_message=f"Failed to save restore upload chunk {chunk_index} for upload '{upload_id}'.",
            exc=e,
        )


@router.post(
    "/upload/{upload_id}/complete", dependencies=[Depends(get_current_active_superuser)]
)
async def complete_upload(
    upload_id: str,
    clear_existing: bool = Query(False),
    overwrite_existing: bool = Query(False),
    current_user: User = Depends(get_current_active_superuser),
    db: AsyncSession = Depends(get_db),
):
    """
    Assemble chunks and trigger restore.
    """
    path_manager = PathManager()

    # Destination for assembled file (same as regular import)
    restore_temp_dir = path_manager.user_data_directory / "temp_restores"
    restore_temp_dir.mkdir(parents=True, exist_ok=True)

    # The original filename is not readily available; the upload ID is used instead.
    final_path = restore_temp_dir / f"{upload_id}_restored.zip"

    try:
        # Assemble file. A malformed upload_id (rejected by get_upload_temp_dir)
        # or a batch with no uploaded parts is a client error, not a 500.
        try:
            path_manager.assemble_upload(upload_id, final_path)
        except ValueError:
            if final_path.exists():
                os.remove(final_path)
            raise HTTPException(status_code=400, detail="Invalid or empty upload.")

        return await _dispatch_restore(
            db=db,
            current_user=current_user,
            archive_path=final_path,
            clear_existing=clear_existing,
            overwrite_existing=overwrite_existing,
        )

    except HTTPException:
        # A deliberate client-error response (e.g. 400) must not be re-wrapped
        # into a generic 500 by the catch-all below.
        raise
    except Exception as e:  # noqa: BLE001
        if final_path.exists():
            os.remove(final_path)
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to finalize the uploaded backup.",
            log_message=f"Failed to finalize restore upload '{upload_id}'.",
            exc=e,
        )
