import asyncio
import logging
import os
import shutil
import time
import uuid

import redis.asyncio as redis
from celery.result import AsyncResult
from fastapi import (
    APIRouter,
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
from backend.core.backup import (
    ARCHIVE_QUALITIES,
    ARCHIVE_QUALITY_COMPRESSED,
    ARCHIVE_QUALITY_ORIGINAL,
    RESTORE_LOCK_KEY,
    RESTORE_LOCK_TTL_SECONDS,
    BackupManager,
    export_job,
    runtime,
)
from backend.core.backup import cache as backup_cache
from backend.core.backup.export import current_fingerprint
from backend.core.backup.export_job import ExportJob
from backend.core.redis import REDIS_URL
from backend.core.task_dispatch import dispatch_task
from backend.models.user import User
from backend.utils.path_manager import PathManager
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.upload_limit import UPLOAD_LIMIT_BACKUP, stream_and_validate_upload

logger = logging.getLogger(__name__)

router = APIRouter()


def _redis_client():
    return redis.from_url(REDIS_URL, decode_responses=True)


def _export_directory():
    return runtime.backup_export_directory(runtime.PathManager())


def _describe_options(include_audio: bool, archive_quality: str) -> str:
    if not include_audio:
        return "a backup without audio"
    if archive_quality == ARCHIVE_QUALITY_ORIGINAL:
        return "an Original-quality backup"
    return "a Compressed backup"


async def _job_is_active(job: ExportJob) -> bool:
    result = AsyncResult(job.task_id, app=celery_app)
    return await asyncio.to_thread(export_job.job_is_active, job, result, time.time())


async def _active_export_job(client) -> ExportJob | None:
    """The export being built, or None if there is none or the recorded one is lost."""
    job = await export_job.read_job(client)
    if job is None or not await _job_is_active(job):
        return None
    return job


def _building(task_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=202, content={"status": "building", "task_id": task_id}
    )


async def _ready_export(
    include_audio: bool, archive_quality: str
) -> JSONResponse | None:
    """The cached backup, if it has these options and nothing has changed since."""
    try:
        cached = await asyncio.to_thread(
            BackupManager.find_current_export, include_audio, archive_quality
        )
    except Exception as e:  # noqa: BLE001 -- boundary: a failed check only costs a rebuild
        logger.warning("Could not check the cached backup; building a new one: %s", e)
        return None
    if cached is None:
        return None
    return JSONResponse(
        status_code=200, content={"status": "ready", "export": cached.summary()}
    )


async def _claim_export(client, job: ExportJob) -> str | None:
    """Record ``job`` as the running export.

    Returns None once it is recorded, or the task id of a build with the same options to
    join instead. Raises 409 while a build with other options is running.
    """
    # Two attempts: the second covers a recorded build that turned out to be lost.
    for _ in range(2):
        if await export_job.claim_job(client, job):
            return None
        existing = await export_job.read_job(client)
        if existing is None:
            continue
        if not await _job_is_active(existing):
            await export_job.release_job(client, existing.task_id)
            continue
        if existing.same_options(job.include_audio, job.archive_quality):
            return existing.task_id
        raise HTTPException(
            status_code=409,
            detail=(
                f"Nojoin is already preparing "
                f"{_describe_options(existing.include_audio, existing.archive_quality)}. "
                "Wait for it to finish, then try again."
            ),
        )
    raise HTTPException(
        status_code=409,
        detail="Another backup is starting. Try again in a moment.",
    )


async def _refuse_restore_during_export() -> None:
    """A restore rewrites the tables and files an export is reading, so they never overlap."""
    client = _redis_client()
    try:
        job = await _active_export_job(client)
    except Exception as e:  # noqa: BLE001 -- boundary: the lock store being down must not refuse every restore
        logger.warning("Export job record unavailable, allowing the restore: %s", e)
        return
    finally:
        await client.aclose()
    if job is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A backup is being prepared on this server. Wait for it to finish "
                "before restoring."
            ),
        )


async def _acquire_restore_lock(job_id: str) -> bool:
    """Claim the install-wide restore lock, or report that one is already running.

    Fails open when Redis is unreachable: refusing every restore because the lock store
    is down would be worse than the race it guards against.
    """
    try:
        client = _redis_client()
        acquired = await client.set(
            RESTORE_LOCK_KEY, job_id, nx=True, ex=RESTORE_LOCK_TTL_SECONDS
        )
        await client.aclose()
        return bool(acquired)
    except Exception as e:  # noqa: BLE001 -- boundary: the lock store being down must not refuse every restore
        logger.warning("Restore lock unavailable, proceeding without it: %s", e)
        return True


async def _release_restore_lock() -> None:
    try:
        client = _redis_client()
        await client.delete(RESTORE_LOCK_KEY)
        await client.aclose()
    except Exception as e:  # noqa: BLE001 -- boundary: the lock also expires on its TTL, so a failed release is not fatal
        logger.warning("Failed to release restore lock: %s", e)


async def _restore_lock_holder() -> str | None:
    """The job id holding the restore lock, or None when no restore is running."""
    client = _redis_client()
    try:
        return await client.get(RESTORE_LOCK_KEY)
    finally:
        await client.aclose()


def _discard_upload(archive_path) -> None:
    try:
        if archive_path.exists():
            os.unlink(archive_path)
    except OSError:
        pass


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

    try:
        await _refuse_restore_during_export()
    except HTTPException:
        _discard_upload(archive_path)
        raise

    # The id is chosen here so the lock can name the job. The status endpoint compares
    # the two to tell a queued restore from one the server has never heard of.
    job_id = str(uuid.uuid4())
    if not await _acquire_restore_lock(job_id):
        _discard_upload(archive_path)
        raise HTTPException(
            status_code=409,
            detail=(
                "A restore is already running on this server. Wait for it to finish "
                "before starting another."
            ),
        )

    try:
        task = await dispatch_task(
            "backend.worker.tasks.restore_backup_task",
            kwargs={
                "zip_path": str(archive_path),
                "clear_existing": clear_existing,
                "overwrite_existing": overwrite_existing,
            },
            task_id=job_id,
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
    Return a current backup, building one in the background if needed.

    Answers 200 with ``{"status": "ready", "export": {...}}`` when the cached archive has
    these options and nothing has changed since it was made, so it can be downloaded at
    once. Otherwise answers 202 with ``{"status": "building", "task_id": str}``, joining
    the export already being built when it has the same options.
    """
    if archive_quality not in ARCHIVE_QUALITIES:
        raise HTTPException(
            status_code=400,
            detail=f"archive_quality must be one of {', '.join(ARCHIVE_QUALITIES)}.",
        )
    if not include_audio:
        # Quality only governs audio, so every backup without it is the same backup.
        archive_quality = ARCHIVE_QUALITY_COMPRESSED

    client = _redis_client()
    try:
        if await client.exists(RESTORE_LOCK_KEY):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A restore is running on this server. Wait for it to finish before "
                    "taking a backup."
                ),
            )

        ready = await _ready_export(include_audio, archive_quality)
        if ready is not None:
            return ready

        job = ExportJob(
            task_id=str(uuid.uuid4()),
            include_audio=include_audio,
            archive_quality=archive_quality,
            requested_at=time.time(),
        )
        joined = await _claim_export(client, job)
        if joined is not None:
            return _building(joined)

        try:
            await dispatch_task(
                "backend.worker.tasks.create_backup_task",
                kwargs={
                    "include_audio": include_audio,
                    "archive_quality": archive_quality,
                },
                task_id=job.task_id,
            )
        except Exception:
            await export_job.release_job(client, job.task_id)
            raise

        from backend.models.task import register_task_ownership

        await register_task_ownership(db, job.task_id, current_user.id)
        return _building(job.task_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 -- boundary: a queue failure becomes one sanitised 500
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to start backup export.",
            log_message="Failed to enqueue backup export task.",
            exc=e,
        )
    finally:
        await client.aclose()


@router.get("/export/latest", dependencies=[Depends(get_current_active_superuser)])
async def get_latest_export():
    """
    Describe the cached backup and any export being built.

    Returns ``{"export": {...} | None, "current": bool | None, "job": {...} | None}``.
    ``current`` says whether the cached archive still matches the installation, and is
    None when there is no archive or the check failed.
    """
    try:
        cached = await asyncio.to_thread(backup_cache.load, _export_directory())
        current = None
        if cached is not None:
            try:
                fingerprint = await asyncio.to_thread(
                    current_fingerprint, cached.include_audio, cached.archive_quality
                )
                current = cached.matches(
                    fingerprint, cached.include_audio, cached.archive_quality
                )
            except Exception as e:  # noqa: BLE001 -- boundary: the panel still lists the archive when the check fails
                logger.warning(
                    "Could not check whether the cached backup is current: %s", e
                )

        client = _redis_client()
        try:
            job = await _active_export_job(client)
        finally:
            await client.aclose()

        return {
            "export": cached.summary() if cached else None,
            "current": current,
            "job": job.summary() if job else None,
        }
    except Exception as e:  # noqa: BLE001 -- boundary: any failure to describe the cache becomes one sanitised 500
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to load the latest backup.",
            log_message="Failed to describe the cached backup.",
            exc=e,
        )


@router.delete("/export/latest", dependencies=[Depends(get_current_active_superuser)])
async def delete_latest_export():
    """Delete the cached backup to reclaim its disk space."""
    deleted = await asyncio.to_thread(backup_cache.discard, _export_directory())
    return {"deleted": deleted}


@router.get("/export/{task_id}", dependencies=[Depends(get_current_active_superuser)])
async def get_export_status(task_id: str):
    """
    Get the status of an export requested with ``task_id``.

    Returns ``{"state": str, "status": str, ...}``. A finished export carries the
    archive it produced or reused under ``export``.
    """
    try:
        client = _redis_client()
        try:
            job = await export_job.read_job(client)
        finally:
            await client.aclose()

        export_dir = _export_directory()

        def _describe():
            return export_job.describe_export(
                task_id,
                result=AsyncResult(task_id, app=celery_app),
                job=job,
                cached=backup_cache.load(export_dir),
                now=time.time(),
            )

        return await asyncio.to_thread(_describe)
    except Exception as e:  # noqa: BLE001 -- boundary: result-backend errors share no base class; all become one sanitised 500
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to load backup export status.",
            log_message=f"Failed to load backup export status for task '{task_id}'.",
            exc=e,
        )


@router.get(
    "/export/{export_id}/download", dependencies=[Depends(get_current_active_superuser)]
)
async def download_export(export_id: str):
    """
    Download the cached backup ``export_id``.

    Answers 410 once a newer backup has replaced it or it has been deleted, so a stale
    link never downloads a different archive from the one it was offered for.
    """
    export_dir = _export_directory()
    cached = await asyncio.to_thread(backup_cache.load, export_dir)
    if cached is None or cached.export_id != export_id:
        raise HTTPException(
            status_code=410,
            detail=(
                "That backup is no longer available. A newer one has replaced it or it "
                "was deleted."
            ),
        )

    # Kept after serving, so a Range request can resume an interrupted download.
    return FileResponse(
        path=backup_cache.archive_path(export_dir, cached),
        filename=cached.download_filename(),
        media_type="application/zip",
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
        except Exception as e:  # noqa: BLE001 -- boundary: the temp file is removed and the failure becomes one sanitised 500
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


def _describe_restore(task_result: AsyncResult, holds_lock: bool):
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
        # Celery reports PENDING for an id it has never seen, and for a finished job
        # whose result has expired. Only a queued restore still holds the lock.
        if not holds_lock:
            return {
                "status": "unknown",
                "progress": "Unknown",
                "error": (
                    "The server is no longer tracking this restore. Reload the page to "
                    "see whether the data came across."
                ),
                "warnings": None,
            }
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


@router.get("/import/{job_id}", dependencies=[Depends(get_current_active_superuser)])
async def get_import_status(job_id: str):
    """
    Get status of restore job.

    Reads the Celery result backend rather than an in-process dictionary, so the status
    survives an API restart and is the same whichever API worker answers.
    """
    try:
        holds_lock = await _restore_lock_holder() == job_id
    except Exception as e:  # noqa: BLE001 -- boundary: with the lock store down, a queued restore is assumed to be queued
        logger.warning("Restore lock unavailable while reading job status: %s", e)
        holds_lock = True

    task_result = AsyncResult(job_id, app=celery_app)
    return await asyncio.to_thread(_describe_restore, task_result, holds_lock)


@router.post("/upload/init", dependencies=[Depends(get_current_active_superuser)])
async def init_upload(filename: str, file_size: int, total_chunks: int):
    """
    Initialize a multipart upload.
    Returns: {"upload_id": str}
    """
    # The UI uploads through this route rather than /import, so the limit must
    # hold here. upload_chunk enforces it again against the bytes written.
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

    # Refused before the upload rather than after it, so nobody sends gigabytes to be
    # turned away. _dispatch_restore checks again once the upload is complete.
    await _refuse_restore_during_export()

    upload_id = str(uuid.uuid4())
    path_manager = PathManager()

    # Create temp directory (get_upload_temp_dir creates it as a side effect).
    path_manager.get_upload_temp_dir(upload_id)

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
    except Exception as e:  # noqa: BLE001 -- boundary: any failure to store the chunk becomes one sanitised 500
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
    except Exception as e:  # noqa: BLE001 -- boundary: the assembled file is removed and the failure becomes one sanitised 500
        if final_path.exists():
            os.remove(final_path)
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to finalize the uploaded backup.",
            log_message=f"Failed to finalize restore upload '{upload_id}'.",
            exc=e,
        )
