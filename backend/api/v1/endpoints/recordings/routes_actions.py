import logging

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.calendar import CalendarEvent
from backend.models.recording import RecordingStatus, RecordingUpdate
from backend.models.recording_public import RecordingPublicRead, serialize_recording
from backend.models.user import User
from backend.processing.speaker_cap import (
    MAX_SPEAKER_CAP,
    MIN_SPEAKER_CAP,
    normalize_speaker_cap,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.document_storage import (
    delete_document_files,
    recording_document_paths,
)
from backend.utils.languages import validate_transcription_language
from backend.utils.recording_storage import delete_recording_artifacts_after_commit
from backend.utils.reprocess_publication import (
    REPROCESS_RUNNING_MESSAGE,
    reprocess_replacement_running,
)

from .helpers import (
    _get_owned_calendar_event,
    _get_owned_recording,
    _recording_has_audio,
    _recording_has_proxy,
    _requeue_for_processing,
    revoke_recording_task,
)
from .router import router

logger = logging.getLogger(__name__)


class CalendarEventLink(BaseModel):
    calendar_event_id: int | None = None


@router.put("/{recording_id}/calendar-event", response_model=RecordingPublicRead)
async def link_recording_calendar_event(
    recording_id: str,
    body: CalendarEventLink,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Link, change or unlink the calendar event for a recording.

    A ``calendar_event_id`` of ``None`` unlinks. A non-null id must belong to
    the current user (verified via the calendar connection join) or the
    request is rejected with 404.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    linked_event: CalendarEvent | None = None
    if body.calendar_event_id is not None:
        linked_event = await _get_owned_calendar_event(
            db, body.calendar_event_id, current_user.id
        )
        recording.calendar_event_id = linked_event.id
    else:
        recording.calendar_event_id = None

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return serialize_recording(
        recording,
        has_proxy=_recording_has_proxy(recording),
        include_calendar_event=True,
        calendar_event=linked_event,
    )


@router.patch("/{recording_id}", response_model=RecordingPublicRead)
async def update_recording(
    recording_id: str,
    recording_update: RecordingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording_update.name is not None:
        recording.name = recording_update.name
    if "transcription_language" in recording_update.model_fields_set:
        recording.transcription_language = recording_update.transcription_language

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.delete("/{recording_id}")
async def delete_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a recording and its associated file.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    # A live capture's publisher holds Transcript before Recording; take the
    # same order so deleting a capture mid-step cannot deadlock against it.
    await db.run_sync(
        lambda session: _load_transcript(session, recording.id, lock=True)
    )
    # Locked, so a worker cannot repoint audio_path between this read and the
    # delete (keep_imported_audio_task) and leave its new file behind.
    await db.refresh(recording, with_for_update=True)

    # The paths are read under the lock, and the files go once the delete has
    # committed, so a failed commit keeps them for the row it leaves.
    artifacts = [(recording.id, recording.audio_path, recording.proxy_path)]
    document_paths = await recording_document_paths(db, [recording.id])

    await db.delete(recording)
    await db.commit()
    delete_recording_artifacts_after_commit(artifacts, logger=logger)
    await delete_document_files(document_paths, logger=logger)

    return {"ok": True}


class ReprocessRequest(BaseModel):
    transcription_backend: str
    whisper_model_size: str | None = None
    parakeet_model: str | None = None
    canary_model: str | None = None
    # Three states, distinguished via model_fields_set: absent leaves the
    # recording's existing cap alone, null clears it back to auto-detect, and an
    # integer sets it. Omitting the field must not silently wipe a cap the user
    # set when the recording was created.
    max_speakers: int | None = Field(
        default=None, ge=MIN_SPEAKER_CAP, le=MAX_SPEAKER_CAP
    )
    # The same three states for the recording's language: absent keeps it,
    # null returns it to the owner's setting, "auto" or a listed code sets it.
    # Written to the recording, so later runs keep it.
    transcription_language: str | None = None

    @field_validator("transcription_language")
    @classmethod
    def _validate_transcription_language(cls, value: str | None) -> str | None:
        return None if value is None else validate_transcription_language(value)


@router.post("/{recording_id}/reprocess", response_model=RecordingPublicRead)
async def reprocess_recording(
    recording_id: str,
    body: ReprocessRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Re-run the full processing pipeline with a caller-chosen transcription engine.
    """
    from backend.utils.config_manager import TRANSCRIPTION_BACKENDS
    from backend.utils.transcription_models import ENGINE_OVERRIDE_CHOICES

    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.is_deleted:
        raise HTTPException(status_code=400, detail="Cannot retry a deleted recording")

    if recording.status in {
        RecordingStatus.UPLOADING,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }:
        raise HTTPException(
            status_code=400,
            detail="Recording is already uploading or processing",
        )

    # A failed recording with no audio left, such as an import whose file was
    # refused, keeps the reason it failed rather than losing it to a run that
    # can only find no audio.
    if recording.status == RecordingStatus.ERROR and not _recording_has_audio(
        recording
    ):
        raise HTTPException(
            status_code=409,
            detail=recording.processing_step or "This recording has no audio.",
        )

    if body.transcription_backend not in TRANSCRIPTION_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown transcription backend: {body.transcription_backend}",
        )

    engine_override: dict = {"transcription_backend": body.transcription_backend}
    for key in ("whisper_model_size", "parakeet_model", "canary_model"):
        value = getattr(body, key)
        if value is None:
            continue
        if value not in ENGINE_OVERRIDE_CHOICES[key]:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown {key}. Choose one of: "
                + ", ".join(ENGINE_OVERRIDE_CHOICES[key]),
            )
        engine_override[key] = value

    if "max_speakers" in body.model_fields_set:
        recording.max_speakers = normalize_speaker_cap(body.max_speakers)
        db.add(recording)
    if "transcription_language" in body.model_fields_set:
        recording.transcription_language = body.transcription_language
        db.add(recording)

    queued_step = f"Queued for reprocessing with {body.transcription_backend}..."
    await _requeue_for_processing(
        db, recording, engine_override=engine_override, queued_step=queued_step
    )

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/{recording_id}/archive", response_model=RecordingPublicRead)
async def archive_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Archive a recording. Archived recordings are hidden from the main list.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    if recording.is_deleted:
        raise HTTPException(
            status_code=400, detail="Cannot archive a deleted recording"
        )

    recording.is_archived = True
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/{recording_id}/restore", response_model=RecordingPublicRead)
async def restore_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Restore an archived or binned recording to the main list.

    A recording binned from Archived comes back to the main list too, and
    ``POST /batch/restore`` follows the same rule.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    recording.is_archived = False
    recording.is_deleted = False

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.post("/{recording_id}/soft-delete", response_model=RecordingPublicRead)
async def soft_delete_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Soft-delete a recording, moving it to the bin (the Deleted view).
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    recording.is_deleted = True
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return serialize_recording(recording, has_proxy=_recording_has_proxy(recording))


@router.delete("/{recording_id}/permanent")
async def permanently_delete_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete a recording and its associated file.

    Only recordings already in the bin can be destroyed, so the two-step flow
    (bin, then destroy) holds server-side for every surface, including MCP.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    # Locked for the same reason as in delete_recording.
    await db.refresh(recording, with_for_update=True)

    if not recording.is_deleted:
        raise HTTPException(
            status_code=409,
            detail=(
                "Only recordings in the bin can be permanently deleted. "
                "Move the recording to the bin first."
            ),
        )

    await revoke_recording_task(recording)

    # Read under the lock and removed after the commit, as in delete_recording.
    artifacts = [(recording.id, recording.audio_path, recording.proxy_path)]
    document_paths = await recording_document_paths(db, [recording.id])

    await db.delete(recording)
    await db.commit()
    delete_recording_artifacts_after_commit(artifacts, logger=logger)
    await delete_document_files(document_paths, logger=logger)

    return {"ok": True}


SPEAKER_INFERENCE_NOT_FINISHED_MESSAGE = (
    "Speakers can be inferred once the recording has finished processing."
)
SPEAKER_INFERENCE_FAILED_RECORDING_MESSAGE = (
    "Processing failed for this recording. Reprocess it before inferring speakers."
)


@router.post("/{recording_id}/infer-speakers")
async def infer_speakers_for_recording(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Re-run speaker inference on an already processed meeting.

    Anything else is refused. The run holds the recording in PROCESSING and
    hands it back as PROCESSED, which would end a live or paused capture
    before its audio was assembled (issue #832).
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    # Locked, so two requests cannot both find it PROCESSED and start two runs.
    await db.refresh(recording, with_for_update=True)
    transcript = await db.run_sync(
        lambda session: _load_transcript(session, recording.id)
    )
    # The pass replacing a retained transcript resolves its speakers itself.
    if reprocess_replacement_running(transcript):
        raise HTTPException(status_code=409, detail=REPROCESS_RUNNING_MESSAGE)
    if recording.status != RecordingStatus.PROCESSED:
        raise HTTPException(
            status_code=409,
            detail=(
                SPEAKER_INFERENCE_FAILED_RECORDING_MESSAGE
                if recording.status == RecordingStatus.ERROR
                else SPEAKER_INFERENCE_NOT_FINISHED_MESSAGE
            ),
        )

    recording.status = RecordingStatus.PROCESSING
    recording.processing_step = "Inferring speakers..."
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = await dispatch_task(
        "backend.worker.tasks.infer_speakers_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    return {
        "status": "queued",
        "message": "Speaker suggestion refresh started in background.",
    }
