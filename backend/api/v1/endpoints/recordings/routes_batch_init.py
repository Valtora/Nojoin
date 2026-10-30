import logging
import shutil
from typing import List, Optional
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api.deps import get_current_recording_client_user, get_current_user, get_db
from backend.models.recording import (
    ClientStatus,
    Recording,
    RecordingInitResponse,
    RecordingStatus,
)
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.document_storage import (
    delete_document_files,
    recording_document_paths,
)
from backend.utils.recording_storage import delete_recording_artifacts_after_commit
from backend.utils.time import utc_now
from backend.utils.transcript_settling import new_capture_settling_state

from .helpers import lock_recording_for_delete, revoke_recording_task
from .router import router

logger = logging.getLogger(__name__)


class BatchRecordingIds(BaseModel):
    recording_ids: List[str]


class BatchRecordingResult(BaseModel):
    """What a batch action did, as the server counts it.

    ``count`` is the recordings the request changed, and ``recording_ids``
    their public ids, so a client can report the change and undo exactly that.
    ``skipped`` is the rest of the distinct ids asked for: recordings not found
    for this user, already where the action would put them, or refused by the
    rule the single-recording route applies.
    """

    ok: bool = True
    count: int
    skipped: int
    recording_ids: List[str]


async def _requested_recordings(
    db: AsyncSession, batch: BatchRecordingIds, user_id: int
) -> tuple[list[Recording], int]:
    """The user's recordings among the ids, in request order, and how many
    distinct ids were asked for."""
    requested = list(dict.fromkeys(batch.recording_ids))
    found = await recordings_module.get_recordings_by_public_ids(
        db, requested, user_id=user_id
    )
    by_public_id = {recording.public_id: recording for recording in found}
    recordings = [
        by_public_id[public_id] for public_id in requested if public_id in by_public_id
    ]
    return recordings, len(requested)


def _batch_result(changed: list[Recording], requested: int) -> BatchRecordingResult:
    return BatchRecordingResult(
        count=len(changed),
        skipped=requested - len(changed),
        recording_ids=[recording.public_id for recording in changed],
    )


@router.post("/batch/archive", response_model=BatchRecordingResult)
async def batch_archive_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Archive multiple recordings. As ``POST /{id}/archive`` refuses one, a
    recording in the bin is left where it is.
    """
    recordings, requested = await _requested_recordings(db, batch, current_user.id)
    changed = [
        recording
        for recording in recordings
        if not recording.is_deleted and not recording.is_archived
    ]
    for recording in changed:
        recording.is_archived = True
        db.add(recording)

    await db.commit()
    logger.info("Batch archive: %s requested, %s archived", requested, len(changed))
    return _batch_result(changed, requested)


@router.post("/batch/restore", response_model=BatchRecordingResult)
async def batch_restore_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Restore multiple archived or binned recordings to the main list, as
    ``POST /{id}/restore`` does: one binned from Archived comes back to the main
    list too.
    """
    recordings, requested = await _requested_recordings(db, batch, current_user.id)
    changed = [
        recording
        for recording in recordings
        if recording.is_deleted or recording.is_archived
    ]
    for recording in changed:
        recording.is_deleted = False
        recording.is_archived = False
        db.add(recording)

    await db.commit()
    logger.info("Batch restore: %s requested, %s restored", requested, len(changed))
    return _batch_result(changed, requested)


@router.post("/batch/soft-delete", response_model=BatchRecordingResult)
async def batch_soft_delete_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Move multiple recordings to the bin.
    """
    recordings, requested = await _requested_recordings(db, batch, current_user.id)
    changed = [recording for recording in recordings if not recording.is_deleted]
    for recording in changed:
        recording.is_deleted = True
        db.add(recording)

    await db.commit()
    logger.info("Batch bin: %s requested, %s binned", requested, len(changed))
    return _batch_result(changed, requested)


@router.post("/batch/permanent", response_model=BatchRecordingResult)
async def batch_permanently_delete_recordings(
    batch: BatchRecordingIds,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete multiple recordings in the bin, and their files.

    The rules of ``DELETE /{id}/permanent`` hold for each: only a recording in
    the bin is destroyed, checked under its row lock, so one restored since the
    client listed it survives, and its Celery task is revoked first. The rest
    are left as they are and counted as skipped.
    """
    recordings, requested = await _requested_recordings(db, batch, current_user.id)

    # Locked in id order, each in the single delete's order (audio lock,
    # Transcript, row), so two batches over the same recordings, or a batch and
    # a user deletion, take the locks in one order and cannot deadlock.
    # One deleted by another request while this waited is skipped.
    recordings = sorted(recordings, key=lambda recording: recording.id)
    locked: list[Recording] = []
    for recording in recordings:
        if await lock_recording_for_delete(db, recording):
            locked.append(recording)
    binned = [recording for recording in locked if recording.is_deleted]

    for recording in binned:
        await revoke_recording_task(recording)

    # Read before any delete is flushed, which would cascade the rows away. The
    # files go once the delete has committed, so a failed commit keeps them.
    artifacts = [
        (recording.id, recording.audio_path, recording.proxy_path)
        for recording in binned
    ]
    document_paths = await recording_document_paths(
        db, [recording.id for recording in binned]
    )
    result = _batch_result(binned, requested)
    for recording in binned:
        await db.delete(recording)

    await db.commit()
    delete_recording_artifacts_after_commit(artifacts, logger=logger)
    await delete_document_files(document_paths, logger=logger)
    logger.info(
        "Batch permanent delete: %s requested, %s deleted", requested, len(binned)
    )
    return result


@router.post("/init", response_model=RecordingInitResponse)
async def init_upload(
    request: Request,
    name: Optional[str] = Query(None, description="Name of the recording"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Initialize a multipart upload.
    """
    active_recording = await recordings_module._get_active_capture_recording_for_user(
        db, current_user.id
    )
    if active_recording is not None:
        raise HTTPException(
            status_code=409,
            detail=recordings_module._build_active_recording_conflict(active_recording),
        )

    unique_filename = f"{uuid4()}.wav"
    file_path = str(recordings_module.recordings_root_dir() / unique_filename)

    if not name:
        name = recordings_module.generate_default_meeting_name()

    recording = Recording(
        name=name,
        audio_path=file_path,
        status=RecordingStatus.UPLOADING,
        # A live capture declares itself here rather than leaving client_status
        # NULL until the first pause. UPLOADING alone does not identify a
        # capture -- file imports sit in UPLOADING while their bytes are sent --
        # so without this the UI has to infer "recording" from the absence of a
        # value, which an import satisfies just as well.
        client_status=ClientStatus.RECORDING,
        user_id=current_user.id,
        last_activity_at=utc_now(),
    )

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    db.add(
        Transcript(
            recording_id=recording.id,
            transcript_status="processing",
            settling_state=new_capture_settling_state(
                capture_epoch=recording.capture_epoch
            ),
        )
    )
    await db.commit()

    temp_dir = recordings_module.recording_upload_temp_dir(recording.id, create=False)
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
        recordings_module.recording_upload_temp_dir(recording.id, create=True)

    return RecordingInitResponse(
        id=recording.public_id,
        name=recording.name,
        capture_epoch=recording.capture_epoch,
    )
