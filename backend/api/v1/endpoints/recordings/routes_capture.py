import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api.deps import get_current_recording_client_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.core.task_dispatch import dispatch_task
from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.models.recording import (
    CaptureSourceReportCreate,
    ClientStatus,
    Recording,
    RecordingCaptureLifecycleResponse,
    RecordingStatus,
    capture_looks_stalled,
)
from backend.models.recording_public import RecordingPublicRead, serialize_recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.live_diarization.files import remove_live_label_state
from backend.processing.speaker_cap import (
    MAX_SPEAKER_CAP,
    MIN_SPEAKER_CAP,
    normalize_speaker_cap,
)
from backend.utils.browser_capture_audio import (
    BROWSER_FLAC_SEGMENT_SUFFIX,
    BrowserAudioFormatError,
)
from backend.utils.capture_resume import mark_capture_resume, persist_resume_boundaries
from backend.utils.document_storage import (
    delete_document_files,
    recording_document_paths,
)
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES
from backend.utils.recording_storage import delete_recording_artifacts_after_commit
from backend.utils.stored_paths import require_recording_path
from backend.utils.time import utc_now
from backend.utils.transcript_settling import update_capture_settling_state
from backend.utils.upload_limit import UPLOAD_LIMIT_SEGMENT, stream_and_validate_upload

from .capture_settling import (
    close_live_words,
    lock_capture_publication,
    seal_live_settling,
)
from .router import router

logger = logging.getLogger(__name__)


def _capture_wall_span_seconds(recording: Recording) -> float | None:
    """Wall-clock seconds between the capture starting and its last activity."""
    started_at = recording.created_at
    last_activity_at = recording.last_activity_at
    if started_at is None or last_activity_at is None:
        return None

    try:
        span = (last_activity_at - started_at).total_seconds()
    except TypeError:
        # Mixed naive/aware timestamps from an older row; not worth failing over.
        return None

    return span if span > 0 else None


def _record_finalize_capture_metrics(
    recording: Recording,
    *,
    chunk_rows,
    duration_seconds,
    finalized_from_paused: bool,
    client_elapsed_seconds: float | None = None,
) -> None:
    """Emit the finalize-time capture diagnostics.

    ``capture_coverage`` compares the audio actually stored against how long the
    capture ran. A browser tab that the OS or the browser suspends stops feeding
    the recorder without surfacing any error, so a healthy-looking recording can
    be missing large stretches of a meeting (issue #166). Coverage is the only
    server-side signal that this happened.

    The ratio uses the recording time the browser measured when it sent one.
    The wall span runs from creation to the last upload, so it also counts
    pauses and the time spent draining an upload backlog after Stop, which
    reported a recording with nothing missing at 90% (issue #417).
    """
    try:
        if finalized_from_paused:
            recordings_module.record_pipeline_metric(
                stage="recording_finalized_from_paused",
                recording_id=recording.id,
                payload={"public_id": recording.public_id},
                log=logger,
            )

        captured_ms = sum(int(row.duration_ms or 0) for row in chunk_rows)
        wall_span_seconds = _capture_wall_span_seconds(recording)
        reference_seconds = client_elapsed_seconds or wall_span_seconds
        payload = {
            "public_id": recording.public_id,
            "chunk_count": len(chunk_rows),
            "captured_seconds": round(captured_ms / 1000.0, 2),
            "final_duration_seconds": (
                round(float(duration_seconds), 2) if duration_seconds else None
            ),
            "wall_span_seconds": (
                round(wall_span_seconds, 2) if wall_span_seconds else None
            ),
            "client_elapsed_seconds": (
                round(client_elapsed_seconds, 2) if client_elapsed_seconds else None
            ),
            "coverage_ratio": (
                round((captured_ms / 1000.0) / reference_seconds, 4)
                if reference_seconds
                else None
            ),
        }
        recordings_module.record_pipeline_metric(
            stage="capture_coverage",
            recording_id=recording.id,
            payload=payload,
            log=logger,
        )
    except Exception as exc:  # noqa: BLE001 -- boundary: metrics must never fail a finalise
        logger.warning(
            "Failed to record finalize capture metrics for recording %s: %s",
            recording.id,
            exc,
        )


def _capture_epoch_query(action: str):
    return Query(
        None,
        ge=0,
        description=(
            f"The capture epoch of the tab sending the {action}, from init or "
            "resume. One from before a resume elsewhere is refused with 409 "
            "capture_superseded. Omitted, the request is not checked."
        ),
    )


def _serialize_capture_track(track_payload):
    if track_payload is None:
        return None
    return {
        "kind": track_payload.kind,
        "label": track_payload.label,
        "enabled": track_payload.enabled,
        "muted": track_payload.muted,
        "ready_state": track_payload.ready_state,
        "settings": track_payload.settings,
    }


@router.post("/{recording_id}/pause", response_model=RecordingCaptureLifecycleResponse)
async def pause_upload(
    recording_id: str,
    epoch: Optional[int] = _capture_epoch_query("pause"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Pause an in-flight recording session while retaining uploaded chunks.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )
    # A late pause from a tab whose capture was resumed elsewhere would mark
    # the capture paused under the tab now running it.
    transcript = await lock_capture_publication(db, recording)
    recordings_module._ensure_capture_epoch(recording, epoch)

    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=409, detail=recordings_module.UPLOAD_CLOSED_DETAIL
        )

    if recording.status != RecordingStatus.PAUSED:
        await close_live_words(db, transcript, recording.id)
        update_capture_settling_state(
            transcript,
            capture_epoch=recording.capture_epoch,
            sealed=False,
            phase="paused",
        )
        recording.status = RecordingStatus.PAUSED
        recording.client_status = ClientStatus.PAUSED
        recording.last_activity_at = utc_now()
        db.add(recording)
        await db.commit()
        await db.refresh(recording)

        try:
            recordings_module.record_pipeline_metric(
                stage="recording_paused",
                recording_id=recording.id,
                payload={"public_id": recording.public_id},
                log=logger,
            )
        except Exception as exc:  # noqa: BLE001 -- boundary: metrics must never fail a pause
            logger.warning(
                "Failed to record pause metric for recording %s: %s",
                recording.id,
                exc,
            )

    return RecordingCaptureLifecycleResponse(
        recording_id=recording.public_id,
        status=recording.status,
        last_sequence=recordings_module._get_last_uploaded_sequence(recording.id),
        capture_epoch=recording.capture_epoch,
    )


@router.post("/{recording_id}/resume", response_model=RecordingCaptureLifecycleResponse)
async def resume_upload(
    recording_id: str,
    epoch: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "The capture epoch of the tab that holds the capture, when that tab "
            "carries on with it. Refused with 409 capture_superseded once a "
            "resume elsewhere has issued a later one. Omitted, the caller takes "
            "the capture over and is issued the next epoch."
        ),
    ),
    reclaim_epoch: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "With no epoch: the capture epoch the tab held before its page "
            "reloaded. A capture still uploading under that epoch is taken over "
            "at once, without waiting for it to stall. Any other value changes "
            "nothing."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Resume a paused recording session, or a capture whose tab is gone.

    A capture that stopped uploading without its pause arriving (the tab closed
    while signed out or offline) is resumed as if paused, once it has been quiet
    for ``STALLED_CAPTURE_AFTER``. A capture still uploading is refused, so no
    other tab or device can take it over (issue #519).

    Taking a capture over issues the next capture epoch, and from then on the
    segments, pauses and finalize of every earlier epoch are refused, so the tab
    that held it cannot overwrite what the new one records (issue #570). The
    tab holding the current epoch resumes without taking over, whether the
    server holds its capture paused or still uploading.

    A reloaded tab sends the epoch it held as ``reclaim_epoch`` and takes its
    capture over straight away, so its old page's late requests are refused
    (issue #778).
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )
    # Two takeovers at once must not both be issued the same epoch.
    transcript = await lock_capture_publication(db, recording)

    if epoch is not None:
        recordings_module._ensure_capture_epoch(recording, epoch)
        _ensure_holder_can_resume(recording)
        resumed_after_stall = False
        if (getattr(transcript, "settling_state", None) or {}).get("phase") != "live":
            await close_live_words(db, transcript, recording.id)
    else:
        resumed_after_stall = _ensure_capture_can_be_taken_over(
            recording, reclaim_epoch
        )
        await close_live_words(db, transcript, recording.id)
        recording.capture_epoch += 1

    stalled_last_activity = recording.last_activity_at
    last_sequence = recordings_module._get_last_uploaded_sequence(recording.id)
    mark_capture_resume(transcript, last_sequence + 1)
    recording.status = RecordingStatus.UPLOADING
    update_capture_settling_state(
        transcript, capture_epoch=recording.capture_epoch, sealed=False, phase="live"
    )
    recording.client_status = ClientStatus.RECORDING
    recording.last_activity_at = utc_now()
    db.add(recording)
    # A resume after a failed stop follows a finalize that sealed the chunks.
    await recordings_module._reopen_recording_audio_cursor(
        db, recording_id=recording.id
    )
    await db.commit()
    await db.refresh(recording)

    if resumed_after_stall:
        try:
            recordings_module.record_pipeline_metric(
                stage="stalled_capture_resumed",
                recording_id=recording.id,
                payload={
                    "public_id": recording.public_id,
                    "last_activity_at": (
                        stalled_last_activity.isoformat()
                        if stalled_last_activity
                        else None
                    ),
                },
                log=logger,
            )
        except Exception as exc:  # noqa: BLE001 -- boundary: metrics must never fail a resume
            logger.warning(
                "Failed to record stalled capture resume metric for recording %s: %s",
                recording.id,
                exc,
            )

    return RecordingCaptureLifecycleResponse(
        recording_id=recording.public_id,
        status=recording.status,
        last_sequence=last_sequence,
        capture_epoch=recording.capture_epoch,
    )


def _ensure_capture_can_be_taken_over(
    recording: Recording, reclaim_epoch: int | None
) -> bool:
    """Refuse to take over a capture that is still uploading.

    Unless the caller held the current epoch before its page reloaded: the
    page that was uploading it is gone.

    Returns whether the capture is taken over because it stalled rather than
    because it was paused.
    """
    resumed_after_stall = capture_looks_stalled(recording)
    still_capturing = (
        recording.status == RecordingStatus.UPLOADING
        and recording.client_status == ClientStatus.RECORDING
    )
    reclaimed = still_capturing and reclaim_epoch == recording.capture_epoch
    if (
        recording.status != RecordingStatus.PAUSED
        and not resumed_after_stall
        and not reclaimed
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                recordings_module.CAPTURE_STILL_UPLOADING_DETAIL
                if still_capturing
                else "Recording is not paused"
            ),
        )
    return resumed_after_stall


def _ensure_holder_can_resume(recording: Recording) -> None:
    """Let the tab holding the current epoch carry on with its own capture.

    No other tab or device can be running it, so a capture the server still
    holds as uploading is resumed too: its pause may never have arrived.
    """
    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=409, detail=recordings_module.UPLOAD_CLOSED_DETAIL
        )
    if (
        recording.status == RecordingStatus.UPLOADING
        and recording.client_status != ClientStatus.RECORDING
    ):
        raise HTTPException(status_code=409, detail="Recording is not paused")


async def _store_uploaded_segment(
    db: AsyncSession,
    *,
    recording_id: int,
    sequence: int,
    segment_suffix: str,
    segment_path: Path,
    temp_dir: Path,
) -> bool:
    """Bank an uploaded segment. Returns whether it reached the live lane.

    FLAC is decoded and registered here, which is all a segment needs before
    live transcription can read it. Older clients' WAV registers directly, and
    their compressed formats wait for the transcode task.
    """
    if segment_suffix == BROWSER_FLAC_SEGMENT_SUFFIX:
        return await recordings_module._accept_browser_flac_segment(
            db,
            recording_id=recording_id,
            sequence=sequence,
            flac_path=segment_path,
            temp_dir=temp_dir,
        )
    if segment_suffix == ".wav":
        row = await recordings_module._register_recording_audio_chunk(
            db,
            recording_id=recording_id,
            sequence=sequence,
            storage_path=segment_path,
            temp_dir=temp_dir,
        )
        return row is not None
    return False


async def _dispatch_segment_followup(
    *, recording_id: int, sequence: int, segment_suffix: str, live_ready: bool
) -> None:
    if live_ready:
        if not recordings_module.config_manager.get("enable_live_transcription"):
            return
        try:
            await dispatch_task(
                "backend.processing.live_transcribe.transcribe_segment_live_task",
                args=[recording_id, sequence],
            )
        except Exception as e:  # noqa: BLE001 -- boundary: live transcription is best-effort; the segment is still stored
            logger.warning(
                "Failed to dispatch live transcription task for recording %s segment %s: %s",
                recording_id,
                sequence,
                e,
            )
        return
    if segment_suffix in BROWSER_AUDIO_SEGMENT_SUFFIXES:
        try:
            await dispatch_task(
                "backend.processing.segment_transcode.transcode_segment_task",
                args=[recording_id, sequence],
            )
        except Exception as e:  # noqa: BLE001 -- boundary: transcoding is retried at finalise; the segment is still stored
            logger.warning(
                "Failed to dispatch segment transcode task for recording %s segment %s: %s",
                recording_id,
                sequence,
                e,
            )


@router.post("/{recording_id}/segment")
async def upload_segment(
    request: Request,
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    epoch: Optional[int] = _capture_epoch_query("segment"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Upload a segment for a recording.
    """
    async with enforce_upload_concurrency(
        request, "upload_segment", str(current_user.id), 5
    ):
        recording = await recordings_module._get_owned_recording(
            db, recording_id, current_user.id
        )
        segment_suffix = recordings_module._resolve_segment_upload_suffix(file)

        recordings_module._ensure_recording_accepts_uploads(recording)

        recording_temp_dir = recordings_module.recording_upload_temp_dir(
            recording.id, create=True
        )

        filename = os.path.basename(f"{int(sequence)}{segment_suffix}")
        segment_path = recording_temp_dir / filename
        # Streamed under a name no directory scan reads, and given its sequence
        # only once the epoch check has passed: a refused segment must never be
        # counted as the last sequence a resume elsewhere numbers after.
        staging_path = recording_temp_dir / f"{filename}.{uuid4().hex}.upload-tmp"

        try:
            await stream_and_validate_upload(
                file=file,
                dest_path=str(staging_path),
                max_size=UPLOAD_LIMIT_SEGMENT,
            )
            await recordings_module._lock_capture(db, recording)
            recordings_module._ensure_capture_epoch(recording, epoch)
            recordings_module._ensure_recording_accepts_uploads(recording)
            os.replace(staging_path, segment_path)
            file_size = segment_path.stat().st_size
            live_ready = await _store_uploaded_segment(
                db,
                recording_id=recording.id,
                sequence=sequence,
                segment_suffix=segment_suffix,
                segment_path=segment_path,
                temp_dir=recording_temp_dir,
            )
            await recordings_module._touch_recording_last_activity(db, recording.id)
            await db.commit()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 -- boundary: the partial segment is removed and the failure becomes one sanitised 500
            try:
                if segment_path.exists():
                    segment_path.unlink()
            except OSError:
                pass
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to save the uploaded segment.",
                log_message=f"Failed to save uploaded segment {sequence} for recording {recording_id}.",
                exc=e,
            )
        finally:
            staging_path.unlink(missing_ok=True)

    try:
        recordings_module.record_pipeline_metric(
            stage="audio_chunk_uploaded",
            recording_id=recording.id,
            payload={
                "sequence": sequence,
                "bytes": file_size,
                "filename": filename,
            },
            log=logger,
        )
    except Exception as exc:  # noqa: BLE001 -- boundary: metrics must never fail an upload
        logger.warning(
            "Failed to record audio chunk upload metric for recording %s segment %s: %s",
            recording.id,
            sequence,
            exc,
        )

    await _dispatch_segment_followup(
        recording_id=recording.id,
        sequence=sequence,
        segment_suffix=segment_suffix,
        live_ready=live_ready,
    )

    return {"status": "received", "segment": sequence}


@router.post("/{recording_id}/capture-source-report")
async def log_capture_source_report(
    recording_id: str,
    payload: CaptureSourceReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    logger.info(
        (
            "Capture source report for recording %s (user %s): "
            "attempt=%s outcome=%s mode=%s requested_microphone_device_id=%s "
            "requested_microphone_label=%s shared_audio_available=%s "
            "configured_microphone_gain=%s configured_system_gain=%s "
            "configured_echo_cancellation=%s configured_noise_suppression=%s "
            "configured_auto_gain_control=%s "
            "failure_code=%s failure_message=%s available_microphones=%s "
            "browser_microphone_track=%s browser_display_audio_track=%s "
            "browser_display_video_track=%s notes=%s"
        ),
        recording.public_id,
        current_user.id,
        payload.attempt_kind,
        payload.outcome,
        payload.mode,
        payload.requested_microphone_device_id,
        payload.requested_microphone_label,
        payload.shared_audio_available,
        payload.configured_microphone_gain,
        payload.configured_system_gain,
        payload.configured_echo_cancellation,
        payload.configured_noise_suppression,
        payload.configured_auto_gain_control,
        payload.failure_code,
        payload.failure_message,
        [device.model_dump() for device in payload.available_microphones],
        _serialize_capture_track(payload.browser_microphone_track),
        _serialize_capture_track(payload.browser_display_audio_track),
        _serialize_capture_track(payload.browser_display_video_track),
        payload.notes,
    )

    return {"status": "logged"}


async def _abandon_failed_master(
    db: AsyncSession, recording: Recording, final_audio_path: str
) -> None:
    """Set a recording's segments aside after its master could not be built."""
    # The segments are kept for recovery; the live speaker labels are not needed.
    remove_live_label_state(
        recordings_module.recording_upload_temp_dir(recording.id, create=False) / "live"
    )
    failed_root: Path | None = None
    try:
        failed_root = recordings_module.move_recording_upload_to_failed(
            recording.id, logger=logger
        )
    except Exception as move_error:  # noqa: BLE001 -- boundary: the move is cleanup; the original error is what the client needs
        logger.error(f"Failed to move segments to failed dir: {move_error}")
    await recordings_module._mark_recording_audio_chunks_failed(
        db,
        recording_id=recording.id,
        failed_root=failed_root,
    )
    await db.commit()

    # The segments (or, if the move failed, temp/<id>/) must stay for the
    # 24-hour chunk sweep the rows above rely on, so remove only the partial
    # master: passing an id here would also rmtree temp/<id>/.
    recordings_module.delete_recording_artifacts(
        recording_id=None,
        audio_path=final_audio_path,
        proxy_path=None,
        logger=logger,
    )


@router.post("/{recording_id}/finalize", response_model=RecordingPublicRead)
async def finalize_upload(
    recording_id: str,
    client_elapsed_seconds: Optional[float] = Query(
        None,
        ge=0,
        description=(
            "Recording time the browser measured, pauses excluded. Used only for "
            "the capture coverage metric."
        ),
    ),
    epoch: Optional[int] = _capture_epoch_query("stop"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Finalize the upload, concatenate segments, and trigger processing.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )
    # Held to the commit below, so no resume can take the capture over while
    # its master is built.
    await recordings_module._lock_capture(db, recording)
    recordings_module._ensure_capture_epoch(recording, epoch)

    recordings_module._ensure_recording_can_finalize_upload(recording)
    # Before the chunk and window rows: a live publisher holds Transcript and
    # then marks windows processed, so taking it later could deadlock.
    await seal_live_settling(db, recording)

    await recordings_module._sync_recording_audio_chunks_from_directory(
        db,
        recording_id=recording.id,
        source_kind="browser",
        suffix=".wav",
    )
    await recordings_module._sync_recording_audio_window_manifests(
        db,
        recording_id=recording.id,
        source_kind="browser",
        seal_tail=True,
    )
    # Live registration stops here. A segment task still queued would otherwise
    # unseal every window this rebuild just sealed and reset its progress.
    await recordings_module._seal_recording_audio_cursor(db, recording_id=recording.id)
    chunk_rows = await recordings_module._list_recording_audio_chunks(
        db,
        recording_id=recording.id,
        source_kind="browser",
    )
    pending_transcode_sequences = recordings_module._find_pending_transcode_sequences(
        recording.id,
        chunk_rows=chunk_rows,
    )
    if pending_transcode_sequences:
        (
            chunk_rows,
            pending_transcode_sequences,
        ) = await recordings_module._rescue_pending_browser_segments_for_finalize(
            db,
            recording_id=recording.id,
            pending_sequences=pending_transcode_sequences,
        )

    if pending_transcode_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )
    if not chunk_rows:
        raise HTTPException(status_code=400, detail="No valid segments found")

    corrupt_sequences = recordings_module._list_corrupt_browser_segment_sequences(
        recording.id
    )
    missing_sequences = [
        sequence
        for sequence in recordings_module._find_missing_chunk_sequences(chunk_rows)
        if sequence not in corrupt_sequences
    ]
    if missing_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )

    transcript = await db.run_sync(
        lambda session: session.execute(
            select(Transcript).where(Transcript.recording_id == recording.id)
        ).scalar_one_or_none()
    )
    persist_resume_boundaries(transcript, chunk_rows)

    final_audio_path = recordings_module._browser_master_output_path(recording)

    def _build_master_audio() -> float:
        """Join the segments into the FLAC master and return its exact length.

        Reading thousands of segment files is blocking I/O, and the API serves
        from a single event loop, so it runs on a worker thread. The master is
        built from the canonical WAVs rather than from what the browser sent,
        so it is sample-exact whatever the client uploaded: joining the WebM
        segments of older clients overlapped every boundary by 60 ms and
        understated the duration by 3% (issue #417).
        """
        return recordings_module.write_browser_master_audio(
            [require_recording_path(row.storage_path) for row in chunk_rows],
            require_recording_path(final_audio_path),
        )

    try:
        duration_seconds = await asyncio.to_thread(_build_master_audio)
    except BrowserAudioFormatError as exc:
        await _abandon_failed_master(db, recording, final_audio_path)
        await recordings_module._mark_recording_upload_error(db, recording, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as e:  # noqa: BLE001 -- boundary: any failure to build the master audio is recorded on the recording and reported
        await _abandon_failed_master(db, recording, final_audio_path)
        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to finalize the uploaded recording.",
            log_message=f"Failed to finalize segmented upload for recording {recording_id}.",
            exc=e,
        )

    file_stats = os.stat(final_audio_path)
    await db.refresh(recording)
    recording.audio_path = final_audio_path
    recording.proxy_path = recordings_module.get_initial_proxy_path(final_audio_path)
    recording.file_size_bytes = file_stats.st_size
    recording.duration_seconds = duration_seconds

    # Re-read above picked up any pause committed while segments were being
    # concatenated. PAUSED finalizes just like UPLOADING (issue #166); only a
    # genuinely closed recording is refused here.
    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        db.add(recording)
        await db.commit()
        await db.refresh(recording)
        raise HTTPException(
            status_code=409,
            detail=recordings_module.UPLOAD_CLOSED_DETAIL,
        )

    finalized_from_paused = recording.status == RecordingStatus.PAUSED

    recording.status = RecordingStatus.QUEUED
    recording.client_status = ClientStatus.IDLE
    recording.processing_started_at = None
    recording.processing_completed_at = None

    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    # The live speaker labels hold centroids and turn embeddings. Nothing reads
    # them once the capture ends, so they go now, not with the temp directory.
    remove_live_label_state(
        recordings_module.recording_upload_temp_dir(recording.id, create=False) / "live"
    )

    _record_finalize_capture_metrics(
        recording,
        chunk_rows=chunk_rows,
        duration_seconds=duration_seconds,
        finalized_from_paused=finalized_from_paused,
        client_elapsed_seconds=client_elapsed_seconds,
    )

    task = await dispatch_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    proxy_task = await dispatch_task(
        "backend.worker.tasks.generate_proxy_task", args=[recording.id]
    )
    if proxy_task:
        await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(
        recording, has_proxy=recordings_module._recording_has_proxy(recording)
    )


@router.post("/{recording_id}/discard")
async def discard_upload(
    recording_id: str,
    reason: Optional[str] = Query(
        None, description="Optional client-provided discard reason"
    ),
    epoch: Optional[int] = _capture_epoch_query("discard"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Discard a recording that has not yet completed.

    This is the single graceful "give up on this meeting" path. It accepts any
    in-flight state: an active or paused live capture (``UPLOADING``/``PAUSED``),
    a meeting waiting in the processing queue (``QUEUED``), or one whose
    pipeline is actively running (``PROCESSING``). Whatever stage it is at, the
    backend revokes any running Celery task, removes every on-disk artefact, and
    deletes the recording row so no manual cancel-then-delete is required.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )
    # Locked, so a worker cannot repoint audio_path between this read and the
    # delete (keep_imported_audio_task) and leave its new file behind.
    await lock_capture_publication(db, recording)
    await db.refresh(recording, with_for_update=True)
    # A tab whose capture continued elsewhere must not delete it from under the
    # tab running it. The list and the dialogs discard without an epoch.
    recordings_module._ensure_capture_epoch(recording, epoch)
    # Nor may a tab delete its capture once it has been stopped, which a stop
    # from a dialog does without issuing an epoch: the meeting is being
    # processed, and the tab's own capture is over (issue #590).
    if epoch is not None and recording.status not in {
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
    }:
        raise HTTPException(
            status_code=409, detail=recordings_module.UPLOAD_CLOSED_DETAIL
        )

    discardable_states = {
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }
    if recording.status not in discardable_states:
        raise HTTPException(
            status_code=400,
            detail="Only in-flight or processing recordings can be discarded",
        )

    if reason:
        logger.info(
            "Discarding recording %s (status=%s) for user %s with reason=%s",
            recording.public_id,
            recording.status.value,
            current_user.id,
            reason,
        )
    else:
        logger.info(
            "Discarding recording %s (status=%s) for user %s",
            recording.public_id,
            recording.status.value,
            current_user.id,
        )

    task_id = recording.celery_task_id

    # Attached documents go with the row by FK cascade, so read their paths
    # first. The lock taken above holds back any document insert until the
    # delete commits, so none can land between this read and the cascade. The
    # files, the upload's segments included, go once the delete has committed,
    # so a failed commit keeps them for the capture it leaves.
    artifacts = [(recording.id, recording.audio_path, recording.proxy_path)]
    document_paths = await recording_document_paths(db, [recording.id])

    await db.execute(
        delete(RecordingAudioChunk).where(
            RecordingAudioChunk.recording_id == recording.id
        )
    )
    await db.execute(
        delete(RecordingAudioWindowManifest).where(
            RecordingAudioWindowManifest.recording_id == recording.id
        )
    )
    await db.execute(delete(Transcript).where(Transcript.recording_id == recording.id))
    await db.execute(delete(Recording).where(Recording.id == recording.id))
    await db.commit()
    # Broker contact can stall. Release the publication fence first, then stop
    # the snapshotted task before removing its files.
    if task_id:
        try:
            recordings_module.celery_app.control.revoke(task_id, terminate=True)
        except Exception:  # noqa: BLE001 -- boundary: a revoke that cannot reach the broker must not block the delete
            pass
    delete_recording_artifacts_after_commit(artifacts, logger=logger)
    await delete_document_files(document_paths, logger=logger)

    return {"ok": True}


@router.put("/{recording_id}/client_status", response_model=RecordingPublicRead)
async def update_client_status(
    recording_id: str,
    status: ClientStatus = Query(..., description="Current status of the client"),
    upload_progress: Optional[int] = Query(
        None, description="Upload progress percentage (0-100)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Update the client status (e.g. RECORDING, PAUSED) for a recording.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    recordings_module._ensure_recording_accepts_status_updates(recording)

    recording.client_status = status
    if upload_progress is not None:
        recording.upload_progress = upload_progress
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return serialize_recording(
        recording, has_proxy=recordings_module._recording_has_proxy(recording)
    )


@router.put("/{recording_id}/max_speakers", response_model=RecordingPublicRead)
async def update_max_speakers(
    recording_id: str,
    max_speakers: Optional[int] = Query(
        None,
        ge=MIN_SPEAKER_CAP,
        le=MAX_SPEAKER_CAP,
        description=(
            "Upper bound on the number of speakers. Omit or send null for "
            "auto-detect, which is the default."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """Set or clear the speaker cap for an in-progress recording.

    Editable for as long as the recording accepts status updates, because
    diarization does not run until the capture is stopped -- so the value
    present when the user hits stop is the value that gets used. That is what
    makes a late joiner survivable: the participant count can be corrected
    after the meeting has already started.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    recordings_module._ensure_recording_accepts_status_updates(recording)

    recording.max_speakers = normalize_speaker_cap(max_speakers)
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return serialize_recording(
        recording, has_proxy=recordings_module._recording_has_proxy(recording)
    )
