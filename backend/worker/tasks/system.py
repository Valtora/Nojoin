from .constants import *


@celery_app.task(
    name="backend.worker.tasks.cleanup_temp_recordings", base=DatabaseTask, bind=True
)
def cleanup_temp_recordings(self):
    """
    Periodic task to clean up old temporary files and failed uploads.
    Runs every 24 hours.
    """
    logger.info("Starting cleanup of temp recordings...")

    cleaned_count = cleanup_recording_audio_chunks(self.session, logger=logger)
    cleaned_count += cleanup_stale_recording_artifacts(max_age_hours=24, logger=logger)

    logger.info(f"Cleanup complete. Removed {cleaned_count} items.")


@celery_app.task(name="backend.worker.tasks.download_models_task", bind=True)
def download_models_task(
    self,
    hf_token: str | None = None,
    whisper_model_size: str | None = None,
    transcription_backend: str | None = None,
    parakeet_model: str | None = None,
    canary_model: str | None = None,
    include_core: bool = True,
):
    """
    Prepare required model assets on disk without retaining models in memory.
    """
    from backend.preload_models import download_models

    def progress_callback(message, progress, speed=None, eta=None, stage=None):
        self.update_state(
            state="PROCESSING",
            meta={
                "progress": progress,
                "message": message,
                "speed": speed,
                "eta": eta,
                "stage": stage,
            },
        )

    logger.info(
        "Starting model preparation task (backend=%s, whisper=%s, include_core=%s).",
        transcription_backend,
        whisper_model_size,
        include_core,
    )
    download_models(
        progress_callback=progress_callback,
        hf_token=hf_token,
        whisper_model_size=whisper_model_size,
        transcription_backend=transcription_backend,
        parakeet_model=parakeet_model,
        canary_model=canary_model,
        include_core=include_core,
    )
    return {"status": "success", "message": "Model preparation complete."}


@celery_app.task(name="backend.worker.tasks.get_worker_device_status", bind=True)
def get_worker_device_status(self):
    """
    Check the worker's available processing device (CUDA/CPU).
    """
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else None
        return {
            "device": device,
            "gpu_name": gpu_name,
            "torch_version": torch.__version__,
        }
    except ImportError:
        return {"device": "cpu", "error": "torch not installed"}
    except Exception as e:  # noqa: BLE001
        return {"device": "unknown", "error": str(e)}


@celery_app.task(name="backend.worker.tasks.create_backup_task", bind=True)
def create_backup_task(
    self, include_audio: bool = True, archive_quality: str = "compressed"
):
    """
    Background task to create a backup zip file.
    Returns the path to the backup file.
    """
    from backend.core.backup import BackupManager

    def report(stage: str, current: int = 0, total: int = 0) -> None:
        # Compressing a large library takes minutes. Without per-file reporting the UI
        # cannot tell a working export from a stalled one, so it reports neither.
        status = stage
        if total:
            status = f"{stage} ({current} of {total})"

        self.update_state(
            state="PROCESSING",
            meta={
                "status": status,
                "stage": stage,
                "current": current,
                "total": total,
            },
        )

    try:
        logger.info(
            "Starting backup task (include_audio=%s, archive_quality=%s)",
            include_audio,
            archive_quality,
        )
        report("Starting backup")

        zip_path, warnings = BackupManager.create_backup_blocking(
            include_audio=include_audio,
            archive_quality=archive_quality,
            progress_callback=report,
        )

        logger.info(f"Backup created successfully at {zip_path}")
        return {"status": "success", "zip_path": zip_path, "warnings": warnings}

    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise e


@celery_app.task(
    name="backend.worker.tasks.generate_proxy_task", base=DatabaseTask, bind=True
)
def generate_proxy_task(self, recording_id: int):
    """
    Generate a high-quality MP3 proxy file for frontend playback.
    """
    from backend.utils.audio import convert_to_proxy_mp3

    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found for proxy generation")
            return

        if not recording.audio_path or not os.path.exists(recording.audio_path):
            logger.error(f"Audio file not found for recording {recording_id}")
            return

        # Define proxy path (same dir, .mp3 extension)
        base_path, _ = os.path.splitext(recording.audio_path)
        proxy_path = f"{base_path}.mp3"

        if _paths_point_to_same_media(recording.audio_path, proxy_path):
            logger.info(
                "Recording %s already uses an MP3 source; reusing it as proxy audio.",
                recording_id,
            )
            recording.proxy_path = recording.audio_path
            session.add(recording)
            session.commit()
            return

        logger.info(f"Generating proxy for recording {recording_id} at {proxy_path}")

        mix_to_mono = _recording_uses_browser_capture(session, recording_id)
        if convert_to_proxy_mp3(
            recording.audio_path, proxy_path, mix_to_mono=mix_to_mono
        ):
            recording.proxy_path = proxy_path
            session.add(recording)
            session.commit()
            logger.info(f"Proxy generated successfully for recording {recording_id}")
        else:
            logger.error(f"Failed to generate proxy for recording {recording_id}")

    except Exception as e:  # noqa: BLE001
        logger.error(f"Error in generate_proxy_task for recording {recording_id}: {e}")
        # Not re-raised because proxy generation is optional/secondary.


@celery_app.task(name="backend.worker.tasks.cleanup_backup_artifacts", bind=True)
def cleanup_backup_artifacts(self, max_age_hours: int = 24):
    """
    Reclaim backup working files.

    The download endpoint used to promise that "the periodic cleanup task" would remove
    exported archives. No such task existed, so every export left a full-size zip in the
    shared volume forever. This is that task.

    Exports age out on a TTL rather than being deleted on first download, which keeps
    range requests and interrupted downloads resumable.
    """
    from pathlib import Path

    from backend.core.backup import (
        BACKUP_EXPORT_DIR,
        RESTORE_STAGING_DIRNAME,
    )
    from backend.utils.path_manager import PathManager

    path_manager = PathManager()
    reclaimed = 0

    targets = [
        Path(BACKUP_EXPORT_DIR),
        path_manager.user_data_directory / "temp_restores",
        path_manager.user_data_directory / "temp_uploads",
        path_manager.user_data_directory / RESTORE_STAGING_DIRNAME,
    ]

    for target in targets:
        reclaimed += path_manager.cleanup_temp_files(
            target, max_age_hours=max_age_hours
        )

    logger.info("Backup artifact cleanup complete. Reclaimed %s items.", reclaimed)
    return {"reclaimed": reclaimed}


@celery_app.task(name="backend.worker.tasks.restore_backup_task", bind=True)
def restore_backup_task(
    self,
    zip_path: str,
    clear_existing: bool = False,
    overwrite_existing: bool = False,
):
    """
    Restore a backup archive.

    Runs here rather than as a FastAPI background task so job state lives in the Celery
    result backend: it survives an API restart, it is visible however many API workers
    are running, and multi-gigabyte extraction happens off the request path.
    """
    import os

    from backend.core.backup import BackupManager

    def report(progress: str) -> None:
        self.update_state(state="PROCESSING", meta={"progress": progress})

    try:
        logger.info("Starting restore task for %s", zip_path)
        report("Queued")

        result = BackupManager.restore_backup_blocking(
            job_id=self.request.id,
            zip_path=zip_path,
            clear_existing=clear_existing,
            overwrite_existing=overwrite_existing,
            progress_callback=report,
        )

        return {
            "status": result.get("status", "completed"),
            "progress": result.get("progress", "Done"),
            "warnings": result.get("warnings"),
        }
    finally:
        # The uploaded archive has served its purpose either way. The periodic sweep is
        # the backstop for a worker that dies before reaching this point.
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except OSError as e:
            logger.warning("Failed to remove restored archive %s: %s", zip_path, e)

        # Let the next restore through. The lock also carries a TTL, so a worker killed
        # before this point does not block restores forever.
        try:
            import redis as _redis

            from backend.core.backup import RESTORE_LOCK_KEY
            from backend.core.redis import REDIS_URL

            _redis.from_url(REDIS_URL).delete(RESTORE_LOCK_KEY)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to release restore lock: %s", e)


@celery_app.task(
    name="backend.worker.tasks.finalize_restored_recording_task",
    base=DatabaseTask,
    bind=True,
)
def finalize_restored_recording_task(self, recording_id: int, needs_proxy: bool = True):
    """
    Rebuild the derived artefacts a restored recording does not carry in its archive.

    Backups store the transcript projection, the audio and the attached documents. The
    canonical utterance graph, the RAG index and the playback proxy are all reproducible
    from those, so they are rebuilt here rather than archived.

    Runs on the io lane and dispatches proxy generation to the cpu lane, because ffmpeg
    belongs on cpu while indexing belongs on io. One task per recording keeps a large
    restore from flooding the queue with four messages per meeting.
    """
    from backend.utils.canonical_pipeline.startup import ensure_canonical_backfill

    session = self.session

    recording = session.get(Recording, recording_id)
    if not recording:
        logger.warning("Recording %s not found for restore finalization", recording_id)
        return

    # 1. Rebuild the canonical utterances from the restored transcript projection. The
    #    projection carries the manual edit flags, so hand corrections survive this.
    try:
        ensure_canonical_backfill(session, recording_id)
        session.commit()
    except Exception as e:  # noqa: BLE001
        session.rollback()
        logger.error(
            "Canonical backfill failed for restored recording %s: %s", recording_id, e
        )

    # 2. Rebuild the RAG index for the transcript and every attached document.
    try:
        celery_app.send_task(
            "backend.worker.tasks.index_transcript_task", args=[recording_id]
        )
        document_ids = session.exec(
            select(Document.id).where(Document.recording_id == recording_id)
        ).all()
        for document_id in document_ids:
            celery_app.send_task(
                "backend.worker.tasks.process_document_task", args=[document_id]
            )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Failed to queue indexing for restored recording %s: %s", recording_id, e
        )

    # 3. Playback proxy, on the cpu lane where ffmpeg lives.
    if needs_proxy:
        try:
            celery_app.send_task(
                "backend.worker.tasks.generate_proxy_task", args=[recording_id]
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to queue proxy generation for restored recording %s: %s",
                recording_id,
                e,
            )


__all__ = [name for name in globals() if not name.startswith("__")]
