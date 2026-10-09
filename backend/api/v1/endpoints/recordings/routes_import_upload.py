import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiofiles
from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import delete, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api.deps import get_current_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.core.task_dispatch import dispatch_task
from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.recording_public import RecordingPublicRead, serialize_recording
from backend.models.user import User
from backend.processing.speaker_cap import (
    MAX_SPEAKER_CAP,
    MIN_SPEAKER_CAP,
    normalize_speaker_cap,
)
from backend.utils.audio import concatenate_binary_files, get_audio_duration
from backend.utils.import_audio import (
    MEDIA_CONTAINER_SUFFIXES,
    ImportRefusedError,
    ImportServerError,
    KeptAudio,
    keep_imported_audio,
)
from backend.utils.import_audio_probe import PROBE_TIMEOUT_S
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.recording_storage import (
    FINALIZE_CLAIM_TOKEN_PREFIX,
    FINALIZING_IMPORT_STEP,
    IMPORT_FINALIZING_CODE,
    finalize_claim_cutoff,
    is_finalizing_import,
    new_finalize_claim_token,
    remove_finalize_leftovers,
)
from backend.utils.time import utc_now
from backend.utils.upload_limit import (
    UPLOAD_LIMIT_LEGACY_RECORDING,
    stream_and_validate_upload,
)

from .helpers import (
    _bootstrap_import_audio_windows,
    _enforce_lossy_bitrate,
    _find_missing_chunk_sequences,
    _get_owned_recording,
    _lock_unless_finalizing_import,
    _mark_recording_audio_chunks_failed,
    _recording_has_proxy,
    _sync_recording_audio_chunks_from_directory,
    generate_default_meeting_name,
    get_initial_proxy_path,
)
from .router import router

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_FORMATS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".webm",
    ".ogg",
    ".flac",
    ".mp4",
    ".wma",
    ".opus",
    *MEDIA_CONTAINER_SUFFIXES,
}

_EXTRACTION_SERVER_FAILURE = (
    "The server could not extract the audio from this file. Try again later; "
    "if it keeps failing, an administrator should check the server logs."
)

_CHUNKED_SERVER_FAILURE_STEP = (
    "The server failed while importing this file. Delete this recording and "
    "import the file again."
)

FINALIZE_CLAIM_LOST_DETAIL = (
    "This recording was deleted or changed while its import was being "
    "finalized, so nothing of the import was kept."
)

# A chunked import in one of these has been finalized already. Finalize answers
# a repeated call with the recording, so a client whose proxy dropped the first
# response can retry it.
_FINALIZED_STATUSES = frozenset(
    {RecordingStatus.QUEUED, RecordingStatus.PROCESSING, RecordingStatus.PROCESSED}
)


def _remove_upload(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove the upload %s: %s", path, exc)


async def _keep_uploaded_audio(file_path: str, filename: str | None) -> KeptAudio:
    """Keep the audio of a saved /import or /upload file, off the event loop.

    On a refusal or a server failure the upload is removed and the matching
    HTTP error raised.
    """
    try:
        return await asyncio.to_thread(keep_imported_audio, file_path)
    except ImportRefusedError as exc:
        _remove_upload(file_path)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except ImportServerError as exc:
        _remove_upload(file_path)
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message=_EXTRACTION_SERVER_FAILURE,
            log_message=f"Failed to keep the audio of uploaded file '{filename}'.",
            exc=exc,
        )


@dataclass
class ImportOptions:
    """Shared query parameters for the import routes.

    Bundled into one dependency so both handlers stay within the argument
    limit and so a new import-time option only has to be added in one place.
    """

    name: Optional[str] = Query(None, description="Custom name for the recording")
    recorded_at: Optional[datetime] = Query(
        None, description="Original recording timestamp"
    )
    max_speakers: Optional[int] = Query(
        None,
        ge=MIN_SPEAKER_CAP,
        le=MAX_SPEAKER_CAP,
        description=(
            "Optional upper bound on the number of speakers. Omit for "
            "auto-detect, which is the default."
        ),
    )


@router.post("/import", response_model=RecordingPublicRead)
async def import_audio(
    file: UploadFile = File(...),
    options: ImportOptions = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Import an external audio recording (e.g., from Zoom, Teams, Google Meet).
    Supports: WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, Opus, and the audio
    track of MKV, MKA, MOV, AVI, M4V, TS, MTS, MPG, MPEG and 3GP files. Only the
    audio is stored (see ``keep_imported_audio``).
    """
    # Validate file extension
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if file_ext not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}",
        )

    # Generate a unique filename to prevent collisions
    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    # Save the file
    try:
        async with aiofiles.open(file_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
                await out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        if os.path.exists(file_path):
            os.remove(file_path)
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded recording.",
            log_message=f"Failed to persist imported audio '{file.filename}'.",
            exc=e,
        )

    file_path = (await _keep_uploaded_audio(file_path, file.filename)).path

    file_stats = os.stat(file_path)

    # Get duration
    duration = 0.0
    try:
        duration = get_audio_duration(file_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to get duration: {e}")

    # Determine recording name
    if options.name:
        recording_name = options.name
    else:
        recording_name = os.path.splitext(file.filename)[0] if file.filename else ""
        if not recording_name or recording_name == "blob":
            recording_name = generate_default_meeting_name()

    recording = Recording(
        name=recording_name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.QUEUED,
        max_speakers=normalize_speaker_cap(options.max_speakers),
        user_id=current_user.id,
    )

    # Override created_at if recorded_at is provided
    recorded_at = options.recorded_at
    if recorded_at:
        # Ensure naive UTC datetime for database compatibility
        if recorded_at.tzinfo is not None:
            recorded_at = recorded_at.astimezone(timezone.utc).replace(tzinfo=None)
        recording.created_at = recorded_at

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    await _bootstrap_import_audio_windows(
        db,
        recording_id=recording.id,
        audio_path=file_path,
    )
    await db.commit()

    # Trigger processing task
    task = await dispatch_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    # Trigger proxy generation task
    if not recording.proxy_path:
        proxy_task = await dispatch_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording.id]
        )
        if proxy_task:
            await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/import/chunked/init", response_model=RecordingPublicRead)
async def init_chunked_import(
    filename: str = Query(..., description="Original filename with extension"),
    options: ImportOptions = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Initialize a chunked import for large files.
    """
    # Validate file extension
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}",
        )

    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    # Determine recording name
    if options.name:
        recording_name = options.name
    else:
        recording_name = os.path.splitext(filename)[0]
        if not recording_name:
            recording_name = generate_default_meeting_name()

    recording = Recording(
        name=recording_name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        status=RecordingStatus.UPLOADING,
        max_speakers=normalize_speaker_cap(options.max_speakers),
        user_id=current_user.id,
    )

    # Override created_at if recorded_at is provided
    recorded_at = options.recorded_at
    if recorded_at:
        if recorded_at.tzinfo is not None:
            recorded_at = recorded_at.astimezone(timezone.utc).replace(tzinfo=None)
        recording.created_at = recorded_at

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    # The row is already committed, so a filesystem failure here would otherwise
    # leave a permanent UPLOADING record that never receives a byte -- one per
    # retry (issue #153). Roll it back and report the cause instead.
    try:
        recordings_module.recording_upload_temp_dir(recording.id, create=True)
    except OSError as e:
        await db.delete(recording)
        await db.commit()
        raise sanitized_http_exception(
            logger=logger,
            status_code=503,
            client_message=(
                "Recording storage is not writable, so the import could not be "
                "started. An administrator should check the ownership of the "
                "directory bound to /app/data."
            ),
            log_message=(
                "Failed to create the chunked import temp directory for recording "
                f"{recording.id}; the recordings storage is not writable."
            ),
            exc=e,
        )

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/import/chunked/segment")
async def upload_chunked_segment(
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a binary segment for a chunked import.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    await _lock_unless_finalizing_import(db, recording)

    if recording.status != RecordingStatus.UPLOADING:
        raise HTTPException(
            status_code=400, detail="Recording is not in uploading state"
        )

    recording_temp_dir = recordings_module.recording_upload_temp_dir(
        recording.id, create=True
    )

    filename = os.path.basename(f"{int(sequence)}.part")
    segment_path = recording_temp_dir / filename

    try:
        async with aiofiles.open(segment_path, "wb") as out_file:
            content = await file.read()
            await out_file.write(content)
        await _sync_recording_audio_chunks_from_directory(
            db,
            recording_id=recording.id,
            source_kind="import_part",
            suffix=".part",
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        try:
            if segment_path.exists():
                segment_path.unlink()
        except OSError:
            pass
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to save the uploaded segment.",
            log_message=f"Failed to save chunked import segment {sequence} for recording {recording_id}.",
            exc=e,
        )

    return {"status": "received", "segment": sequence}


@dataclass(frozen=True)
class _Claim:
    """One finalize attempt's claim on an import.

    ``token`` is stored with the claim (see ``FINALIZING_IMPORT_STEP``), and
    every file the attempt writes carries its id: ``source_path`` is the
    reassembled upload, and ``keep_imported_audio`` names the audio it
    extracts after it. Kept apart from the ORM row, which a rollback expires.
    """

    recording_id: int
    public_id: str
    token: str
    source_path: str


def _claim_for_attempt(recording: Recording, token: str) -> _Claim:
    upload = Path(recording.audio_path)
    attempt = token.removeprefix(FINALIZE_CLAIM_TOKEN_PREFIX)
    return _Claim(
        recording_id=recording.id,
        public_id=recording.public_id,
        token=token,
        source_path=str(upload.with_name(f"{upload.stem}.{attempt}{upload.suffix}")),
    )


def _claim_held(token: str):
    """The row still carries the claim ``token`` names."""
    return (
        (Recording.status == RecordingStatus.UPLOADING)
        & (Recording.processing_step == FINALIZING_IMPORT_STEP)
        & (Recording.celery_task_id == token)
    )


def _answer_unclaimable_finalize(recording: Recording) -> RecordingPublicRead:
    """Answer a finalize for an import that is not waiting to be finalized.

    Already finalized: return it, so a client whose proxy dropped the first
    answer can retry. Being finalized by another call: 409, so the client can
    wait and ask again. Failed or released: 400 with the reason the recording
    shows. Anything else: 400, as before.
    """
    if recording.status in _FINALIZED_STATUSES:
        return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
    if is_finalizing_import(recording):
        raise HTTPException(
            status_code=409,
            detail={
                "code": IMPORT_FINALIZING_CODE,
                "message": "This import is already being finalized.",
            },
        )
    if recording.status == RecordingStatus.ERROR and recording.processing_step:
        raise HTTPException(status_code=400, detail=recording.processing_step)
    raise HTTPException(status_code=400, detail="Recording is not in uploading state")


async def _claim_for_finalize(
    db: AsyncSession, recording: Recording, token: str
) -> bool:
    """Claim ``recording`` for the finalize attempt ``token`` names, atomically.

    A conditional UPDATE, so of two finalize calls exactly one claims the row.
    It matches an UPLOADING import that no finalize holds, or whose claim is
    stale (``finalize_claim_cutoff``), which is taken over. Only the claim is
    written under a lock; the caller commits it at once and holds no
    connection while the audio is extracted.
    """
    result = await db.execute(
        update(Recording)
        .where(Recording.id == recording.id)
        .where(Recording.status == RecordingStatus.UPLOADING)
        .where(
            Recording.processing_step.is_distinct_from(FINALIZING_IMPORT_STEP)
            | (Recording.updated_at <= finalize_claim_cutoff())
        )
        .values(
            processing_step=FINALIZING_IMPORT_STEP,
            celery_task_id=token,
            updated_at=utc_now(),
        )
    )
    return result.rowcount == 1


async def _settle_claim(db: AsyncSession, claim: _Claim, **values) -> bool:
    """Write ``values`` if the row still carries this attempt's claim.

    It does not when the recording was deleted, discarded or changed
    meanwhile, or another finalize took the claim over. On success the row
    stays locked until the caller commits.
    """
    result = await db.execute(
        update(Recording)
        .where(Recording.id == claim.recording_id)
        .where(_claim_held(claim.token))
        .values(**values)
    )
    return result.rowcount == 1


async def _answer_lost_claim(
    db: AsyncSession, recording: Recording, claim: _Claim, kept_path: str | None
) -> RecordingPublicRead:
    """Answer for an attempt that no longer holds its claim.

    Only this attempt's own files are removed; whoever holds the claim now
    owns the rest. The answer is what a repeated call gets, or 409 when the
    recording is gone.
    """
    for path in (claim.source_path, kept_path):
        if path is not None:
            _remove_upload(path)
    try:
        await db.refresh(recording)
    except InvalidRequestError:
        raise HTTPException(status_code=409, detail=FINALIZE_CLAIM_LOST_DETAIL)
    return _answer_unclaimable_finalize(recording)


async def _discard_chunked_import(db: AsyncSession, claim: _Claim) -> bool:
    """Remove a refused chunked import: its files, chunk rows and recording.

    False, with nothing removed, when this attempt no longer holds the claim.
    """
    await db.execute(
        delete(RecordingAudioChunk).where(
            RecordingAudioChunk.recording_id == claim.recording_id
        )
    )
    await db.execute(
        delete(RecordingAudioWindowManifest).where(
            RecordingAudioWindowManifest.recording_id == claim.recording_id
        )
    )
    result = await db.execute(
        delete(Recording)
        .where(Recording.id == claim.recording_id)
        .where(_claim_held(claim.token))
    )
    if result.rowcount != 1:
        await db.rollback()
        return False
    await db.commit()
    recordings_module.delete_recording_artifacts(
        recording_id=claim.recording_id,
        audio_path=claim.source_path,
        proxy_path=None,
        logger=logger,
    )
    return True


async def _fail_chunked_finalize(
    db: AsyncSession, claim: _Claim, kept_path: str | None = None
) -> bool:
    """Settle a finalize the server failed: keep the parts, mark the import.

    The recording is marked ERROR so it does not stay claimed, the parts move
    to ``failed/`` for recovery, as for any server failure in finalize, and
    this attempt's reassembled upload and extracted audio are deleted. False,
    with nothing changed, when this attempt no longer holds the claim.
    """
    settled = await _settle_claim(
        db,
        claim,
        status=RecordingStatus.ERROR,
        client_status=ClientStatus.IDLE,
        processing_step=_CHUNKED_SERVER_FAILURE_STEP,
        celery_task_id=None,
    )
    if not settled:
        await db.rollback()
        return False
    failed_root: Path | None = None
    try:
        failed_root = recordings_module.move_recording_upload_to_failed(
            claim.recording_id, logger=logger
        )
    except OSError as move_error:
        logger.error(
            f"Failed to move failed chunked upload to failed dir: {move_error}"
        )
    await _mark_recording_audio_chunks_failed(
        db, recording_id=claim.recording_id, failed_root=failed_root
    )
    await db.commit()
    for path in (claim.source_path, kept_path):
        if path is not None:
            _remove_upload(path)
    return True


def _server_failure(claim: _Claim, exc: Exception) -> HTTPException:
    return sanitized_http_exception(
        logger=logger,
        status_code=500,
        client_message=(
            _EXTRACTION_SERVER_FAILURE
            if isinstance(exc, ImportServerError)
            else "Failed to finalize the uploaded recording."
        ),
        log_message=f"Failed to finalize chunked import for recording {claim.public_id}.",
        exc=exc,
    )


@dataclass(frozen=True)
class _KeptImport:
    path: str
    size_bytes: int
    duration_seconds: float | None


def _reassemble_and_keep(segment_paths: list[str], source_path: str) -> _KeptImport:
    """Reassemble the parts and keep their audio. Blocking: run it off the loop.

    Every step but the reassembly, which is disk-bound, is under a timeout.
    The duration is optional, as on /import and /upload. If anything fails
    once the audio is kept, the kept file is removed: no caller knows it yet.
    """
    concatenate_binary_files(segment_paths, source_path)
    kept_path = keep_imported_audio(source_path).path
    try:
        duration: float | None = None
        try:
            duration = get_audio_duration(kept_path, timeout=PROBE_TIMEOUT_S)
        except (RuntimeError, OSError) as e:
            logger.warning(f"Failed to get duration: {e}")
        return _KeptImport(kept_path, os.stat(kept_path).st_size, duration)
    except BaseException:
        _remove_upload(kept_path)
        raise


async def _store_finalized_import(
    db: AsyncSession, recording: Recording, claim: _Claim, kept: _KeptImport
) -> bool:
    """Point ``recording`` at its kept audio, rebuild its window, queue it.

    False, with nothing written, when this attempt no longer holds the claim.
    """
    settled = await _settle_claim(
        db,
        claim,
        audio_path=kept.path,
        proxy_path=get_initial_proxy_path(kept.path),
        file_size_bytes=kept.size_bytes,
        duration_seconds=kept.duration_seconds,
        status=RecordingStatus.QUEUED,
        client_status=ClientStatus.IDLE,
        processing_step=None,
        celery_task_id=None,
    )
    if not settled:
        await db.rollback()
        return False
    await db.execute(
        delete(RecordingAudioChunk)
        .where(RecordingAudioChunk.recording_id == claim.recording_id)
        .where(RecordingAudioChunk.source_kind == "import_part")
    )
    await db.execute(
        delete(RecordingAudioWindowManifest)
        .where(RecordingAudioWindowManifest.recording_id == claim.recording_id)
        .where(RecordingAudioWindowManifest.source_kind == "import_part")
    )
    await _bootstrap_import_audio_windows(
        db, recording_id=claim.recording_id, audio_path=kept.path
    )
    # Read back while the UPDATE still holds the row, so a delete cannot
    # land between the commit and the read.
    await db.refresh(recording)
    await db.commit()
    return True


async def _keep_claimed_import(
    db: AsyncSession, recording: Recording, claim: _Claim, chunk_rows: list
) -> RecordingPublicRead | None:
    """Reassemble a claimed import, keep its audio and queue it.

    None once it is queued. Every way out writes to the row only while this
    attempt still holds the claim; when it does not, the answer is returned.
    """
    segment_paths = [row.storage_path for row in chunk_rows]
    try:
        kept = await asyncio.to_thread(
            _reassemble_and_keep, segment_paths, claim.source_path
        )
    except ImportRefusedError as exc:
        # Refused like /import and /upload: nothing of the upload is kept, so
        # no failed recording is left in the library.
        if not await _discard_chunked_import(db, claim):
            return await _answer_lost_claim(db, recording, claim, None)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:  # noqa: BLE001
        if not await _fail_chunked_finalize(db, claim):
            return await _answer_lost_claim(db, recording, claim, None)
        raise _server_failure(claim, e)

    try:
        stored = await _store_finalized_import(db, recording, claim, kept)
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        if not await _fail_chunked_finalize(db, claim, kept.path):
            return await _answer_lost_claim(db, recording, claim, kept.path)
        raise _server_failure(claim, e)
    if not stored:
        return await _answer_lost_claim(db, recording, claim, kept.path)
    return None


@router.post("/import/chunked/finalize", response_model=RecordingPublicRead)
async def finalize_chunked_import(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Finalize a chunked import, reassemble the file, and trigger processing.

    The recording stays UPLOADING and is claimed (``_claim_for_finalize``) in
    a short transaction committed before the audio is extracted, so no row
    lock or connection is held through the extraction. A second call returns
    at once: the recording when it is already finalized, 409 while it is being
    finalized. A claim older than two hours may be taken over by the next
    finalize; the daily cleanup releases it.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    if recording.status != RecordingStatus.UPLOADING or is_finalizing_import(recording):
        return _answer_unclaimable_finalize(recording)

    await _sync_recording_audio_chunks_from_directory(
        db,
        recording_id=recording.id,
        source_kind="import_part",
        suffix=".part",
    )
    chunk_rows = await recordings_module._list_recording_audio_chunks(
        db,
        recording_id=recording.id,
        source_kind="import_part",
    )
    if not chunk_rows:
        raise HTTPException(status_code=400, detail="No valid segments found")

    missing_sequences = _find_missing_chunk_sequences(chunk_rows)
    if missing_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )

    token = new_finalize_claim_token()
    if not await _claim_for_finalize(db, recording, token):
        await db.rollback()
        try:
            await db.refresh(recording)
        except InvalidRequestError:
            # Another finalize refused the import and removed it meanwhile.
            raise HTTPException(status_code=404, detail="Recording not found")
        return _answer_unclaimable_finalize(recording)
    await db.commit()

    # A stale claim taken over leaves what its attempt wrote; start clean.
    remove_finalize_leftovers(recording.audio_path, logger=logger)
    answer = await _keep_claimed_import(
        db, recording, _claim_for_attempt(recording, token), chunk_rows
    )
    if answer is not None:
        return answer

    task = await dispatch_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    if not recording.proxy_path:
        proxy_task = await dispatch_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording.id]
        )
        if proxy_task:
            await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/upload", response_model=RecordingPublicRead)
async def upload_recording(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a new audio recording.
    """
    # Generate a unique filename to prevent collisions
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if not file_ext:
        file_ext = ".wav"  # Default to wav if unknown

    # Validate extension
    if file_ext not in SUPPORTED_AUDIO_FORMATS and file_ext != ".wav":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{file_ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}",
        )

    unique_filename = f"{uuid4()}{file_ext}"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    # Save the file
    async with enforce_upload_concurrency(
        request, "upload_recording", str(current_user.id), 2
    ):
        try:
            await stream_and_validate_upload(
                file=file,
                dest_path=file_path,
                max_size=UPLOAD_LIMIT_LEGACY_RECORDING,
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to save the uploaded recording.",
                log_message=f"Failed to persist uploaded recording '{file.filename}'.",
                exc=e,
            )

    # The floor judges the uploaded audio: the stored file when it was kept
    # or copied (so a video track's bitrate cannot lift it over), the source
    # track's reported bitrate when it was re-encoded.
    kept = await _keep_uploaded_audio(file_path, file.filename)
    file_path = kept.path

    try:
        if kept.reencoded_from_lossy:
            _enforce_lossy_bitrate(kept.source_bit_rate)
        else:
            recordings_module._enforce_lossy_audio_bitrate_floor(file_path)
    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    file_stats = os.stat(file_path)

    duration = 0.0
    try:
        duration = recordings_module.get_audio_duration(file_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to get duration: {e}")

    # Create DB entry
    name = os.path.splitext(file.filename)[0]
    if name == "blob":  # Common default name from blobs
        name = generate_default_meeting_name()

    recording = Recording(
        name=name,
        proxy_path=get_initial_proxy_path(file_path),
        audio_path=file_path,
        file_size_bytes=file_stats.st_size,
        duration_seconds=duration,
        status=RecordingStatus.QUEUED,
        user_id=current_user.id,
    )

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = await dispatch_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    if not recording.proxy_path:
        proxy_task = await dispatch_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording.id]
        )
        if proxy_task:
            await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))
