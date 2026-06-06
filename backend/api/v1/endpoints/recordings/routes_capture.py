import os
import logging
import asyncio
from typing import Optional
from fastapi import Depends, HTTPException, Query, Request, UploadFile, File
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.utils.time import utc_now

from backend.api.deps import get_db, get_current_recording_client_user
from backend.models.user import User
from backend.models.recording import (
    Recording,
    RecordingCaptureLifecycleResponse,
    RecordingStatus,
    ClientStatus,
)
from backend.models.transcript import Transcript
from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.upload_limit import stream_and_validate_upload, UPLOAD_LIMIT_SEGMENT
from backend.api.error_handling import sanitized_http_exception
from backend.models.recording_public import serialize_recording, RecordingPublicRead

from .router import router
import backend.api.v1.endpoints.recordings as recordings_module

logger = logging.getLogger(__name__)


@router.post("/{recording_id}/pause", response_model=RecordingCaptureLifecycleResponse)
async def pause_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Pause an in-flight recording session while retaining uploaded chunks.
    """
    recording = await recordings_module._get_owned_recording(db, recording_id, current_user.id)

    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(status_code=409, detail=recordings_module.UPLOAD_CLOSED_DETAIL)

    if recording.status != RecordingStatus.PAUSED:
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
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to record pause metric for recording %s: %s",
                recording.id,
                exc,
            )

    return RecordingCaptureLifecycleResponse(
        recording_id=recording.public_id,
        status=recording.status,
        last_sequence=recordings_module._get_last_uploaded_sequence(recording.id),
    )


@router.post("/{recording_id}/resume", response_model=RecordingCaptureLifecycleResponse)
async def resume_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Resume a paused recording session.
    """
    recording = await recordings_module._get_owned_recording(db, recording_id, current_user.id)

    if recording.status != RecordingStatus.PAUSED:
        raise HTTPException(
            status_code=409,
            detail="Recording is not paused",
        )

    recording.status = RecordingStatus.UPLOADING
    recording.client_status = ClientStatus.UPLOADING
    recording.last_activity_at = utc_now()
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return RecordingCaptureLifecycleResponse(
        recording_id=recording.public_id,
        status=recording.status,
        last_sequence=recordings_module._get_last_uploaded_sequence(recording.id),
    )


@router.post("/{recording_id}/segment")
async def upload_segment(
    request: Request,
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Upload a segment for a recording.
    """
    async with enforce_upload_concurrency(request, "upload_segment", str(current_user.id), 5):
        recording = await recordings_module._get_owned_recording(db, recording_id, current_user.id)
        segment_suffix = recordings_module._resolve_segment_upload_suffix(file)

        recordings_module._ensure_recording_accepts_uploads(recording)
        
        recording_temp_dir = recordings_module.recording_upload_temp_dir(recording.id, create=True)
            
        filename = os.path.basename(f"{int(sequence)}{segment_suffix}")
        segment_path = recording_temp_dir / filename
        
        try:
            await stream_and_validate_upload(
                file=file,
                dest_path=str(segment_path),
                max_size=UPLOAD_LIMIT_SEGMENT,
            )
            if segment_suffix == ".wav":
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
                    seal_tail=False,
                )
            recording.last_activity_at = utc_now()
            db.add(recording)
            await db.commit()
        except HTTPException:
            raise
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
                log_message=f"Failed to save uploaded segment {sequence} for recording {recording_id}.",
                exc=e,
            )

    try:
        file_size = segment_path.stat().st_size if segment_path.exists() else 0
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
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to record audio chunk upload metric for recording %s segment %s: %s",
            recording.id,
            sequence,
            exc,
        )

    # Dispatch the live transcription task.
    if segment_suffix == ".wav" and recordings_module.config_manager.get("enable_live_transcription"):
        try:
            recordings_module.celery_app.send_task(
                "backend.processing.live_transcribe.transcribe_segment_live_task",
                args=[recording.id, sequence]
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to dispatch live transcription task for recording %s segment %s: %s",
                recording.id,
                sequence,
                e,
            )
    elif segment_suffix in BROWSER_AUDIO_SEGMENT_SUFFIXES:
        try:
            recordings_module.celery_app.send_task(
                "backend.processing.segment_transcode.transcode_segment_task",
                args=[recording.id, sequence]
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to dispatch segment transcode task for recording %s segment %s: %s",
                recording.id,
                sequence,
                e,
            )

    return {"status": "received", "segment": sequence}


@router.post("/{recording_id}/finalize", response_model=RecordingPublicRead)
async def finalize_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Finalize the upload, concatenate segments, and trigger processing.
    """
    recording = await recordings_module._get_owned_recording(db, recording_id, current_user.id)

    recordings_module._ensure_recording_can_finalize_upload(recording)

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
        await recordings_module._transcode_pending_browser_segments_for_finalize(
            recording.id,
            pending_transcode_sequences,
        )
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
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )
    if not chunk_rows:
        raise HTTPException(status_code=400, detail="No valid segments found")

    missing_sequences = recordings_module._find_missing_chunk_sequences(chunk_rows)
    if missing_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )

    final_audio_path = recording.audio_path

    try:
        master_segment_paths = recordings_module._list_staged_browser_master_segments(recording.id)
        if master_segment_paths:
            final_audio_path = recordings_module._browser_master_output_path(
                recording,
                recordings_module._resolve_browser_master_suffix(master_segment_paths),
            )
            recordings_module.concatenate_media_files(
                [str(path) for path in master_segment_paths],
                final_audio_path,
            )
        else:
            segment_paths = [row.storage_path for row in chunk_rows]
            recordings_module.concatenate_wavs(segment_paths, final_audio_path)

        recordings_module._enforce_lossy_audio_bitrate_floor(final_audio_path)
        duration_seconds = recordings_module.get_audio_duration(final_audio_path)
    except HTTPException as exc:
        failed_root: Path | None = None
        try:
            failed_root = recordings_module.move_recording_upload_to_failed(recording.id, logger=logger)
        except Exception as move_error:  # noqa: BLE001
            logger.error(f"Failed to move segments to failed dir: {move_error}")
        await recordings_module._mark_recording_audio_chunks_failed(
            db,
            recording_id=recording.id,
            failed_root=failed_root,
        )
        await db.commit()

        recordings_module.delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=final_audio_path,
            proxy_path=None,
            logger=logger,
        )
        await recordings_module._mark_recording_upload_error(db, recording, str(exc.detail))
        raise
    except Exception as e:  # noqa: BLE001
        failed_root: Path | None = None
        try:
            failed_root = recordings_module.move_recording_upload_to_failed(recording.id, logger=logger)
        except Exception as move_error:  # noqa: BLE001
            logger.error(f"Failed to move segments to failed dir: {move_error}")
        await recordings_module._mark_recording_audio_chunks_failed(
            db,
            recording_id=recording.id,
            failed_root=failed_root,
        )
        await db.commit()
            
        recordings_module.delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=final_audio_path,
            proxy_path=None,
            logger=logger,
        )
            
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

    if recording.status != RecordingStatus.UPLOADING:
        db.add(recording)
        await db.commit()
        await db.refresh(recording)
        raise HTTPException(
            status_code=409,
            detail=recordings_module.UPLOAD_CLOSED_DETAIL,
        )

    recording.status = RecordingStatus.QUEUED
    recording.client_status = ClientStatus.IDLE
    recording.processing_started_at = None
    recording.processing_completed_at = None
    
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.process_recording_task",
        args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership
    await register_task_ownership(db, task.id, recording.user_id)

    proxy_task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.generate_proxy_task",
        args=[recording.id]
    )
    if proxy_task:
        await register_task_ownership(db, proxy_task.id, recording.user_id)
    
    return serialize_recording(recording, has_proxy=recordings_module._recording_has_proxy(recording))


@router.post("/{recording_id}/discard")
async def discard_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Discard an in-flight recording before it is finalised.
    """
    recording = await recordings_module._get_owned_recording(db, recording_id, current_user.id)

    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=400,
            detail="Only in-flight uploads can be discarded",
        )

    recordings_module.delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )

    await db.execute(
        delete(RecordingAudioChunk).where(RecordingAudioChunk.recording_id == recording.id)
    )
    await db.execute(
        delete(RecordingAudioWindowManifest).where(
            RecordingAudioWindowManifest.recording_id == recording.id
        )
    )
    await db.execute(delete(Transcript).where(Transcript.recording_id == recording.id))
    await db.execute(delete(Recording).where(Recording.id == recording.id))
    await db.commit()

    return {"ok": True}


@router.put("/{recording_id}/client_status", response_model=RecordingPublicRead)
async def update_client_status(
    recording_id: str,
    status: ClientStatus = Query(..., description="Current status of the client"),
    upload_progress: Optional[int] = Query(None, description="Upload progress percentage (0-100)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user)
):
    """
    Update the client status (e.g. RECORDING, PAUSED) for a recording.
    """
    recording = await recordings_module._get_owned_recording(db, recording_id, current_user.id)

    recordings_module._ensure_recording_accepts_status_updates(recording)

    recording.client_status = status
    if upload_progress is not None:
        recording.upload_progress = upload_progress
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return serialize_recording(recording, has_proxy=recordings_module._recording_has_proxy(recording))
