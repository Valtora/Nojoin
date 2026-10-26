from .constants import *


@celery_app.task(
    name="backend.worker.tasks.cleanup_temp_recordings", base=DatabaseTask, bind=True
)
def cleanup_temp_recordings(self):
    """
    Periodic task to clean up old temporary files and failed uploads, then
    report or remove the stored files no row names (storage hygiene).
    Runs every 24 hours.
    """
    logger.info("Starting cleanup of temp recordings...")

    from backend.processing.audio_preprocessing import (
        cleanup_stale_pipeline_temp_files,
    )

    cleaned_count = cleanup_recording_audio_chunks(self.session, logger=logger)
    cleaned_count += cleanup_stale_recording_artifacts(max_age_hours=24, logger=logger)
    cleaned_count += cleanup_stale_pipeline_temp_files(max_age_hours=24)
    cleaned_count += cleanup_orphaned_uploading_recordings(
        self.session, logger=logger, max_age_hours=24
    )

    logger.info(f"Cleanup complete. Removed {cleaned_count} items.")

    # Last, so the sweeps above have already run. It reads in a session of its
    # own, opened after it lists the directories. Imported inside the guard, so
    # a hygiene module that fails to load stops only this step (#660).
    try:
        from backend.utils.storage_hygiene import (
            STORAGE_HYGIENE_DELETE,
            HygieneOptions,
            run_storage_hygiene,
            storage_hygiene_mode,
        )

        run_storage_hygiene(
            get_sync_session,
            HygieneOptions(delete=storage_hygiene_mode() == STORAGE_HYGIENE_DELETE),
        )
    except Exception:  # noqa: BLE001 -- boundary: hygiene is the last, optional step; the sweeps above already ran and must still count as done
        logger.exception("Storage hygiene failed; the cleanup sweeps above completed")


@celery_app.task(
    name="backend.worker.tasks.sweep_speaker_frames_task", base=DatabaseTask, bind=True
)
def sweep_speaker_frames_task(self):
    """Delete the screen study frames that are due (#653). Hourly.

    Runs whatever ``speaker_frame_sampling`` says, so switching the study off
    never keeps a frame past its date. Logs one line per recording it deletes.
    """
    from backend.utils.speaker_frames.retention import sweep_speaker_frames

    result = sweep_speaker_frames(self.session)
    return {
        "deleted_recordings": len(result.deleted_recordings),
        "deleted_frames": result.deleted_frames,
        "kept": result.kept,
    }


@celery_app.task(name="backend.worker.tasks.download_models_task", bind=True)
def download_models_task(
    self,
    hf_token: str | None = None,
    whisper_model_size: str | None = None,
    transcription_backend: str | None = None,
    parakeet_model: str | None = None,
    canary_model: str | None = None,
    include_core: bool = True,
    *,
    nemotron_only: bool = False,
):
    """Prepare required assets on disk without retaining models in memory."""
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
    try:
        kwargs = {"nemotron_only": True} if nemotron_only else {}
        download_models(
            progress_callback=progress_callback,
            hf_token=hf_token,
            whisper_model_size=whisper_model_size,
            transcription_backend=transcription_backend,
            parakeet_model=parakeet_model,
            canary_model=canary_model,
            include_core=include_core,
            **kwargs,
        )
    finally:
        if nemotron_only:
            from backend.utils.nemotron_lock import complete_pending

            complete_pending(self.request.id)
    return {"status": "success", "message": "Model preparation complete."}


@celery_app.task(name="backend.worker.tasks.delete_model_task", bind=True)
def delete_model_task(self, model_name: str, variant: str | None = None):
    """
    Remove a cached model from the shared model volume.

    Deletion runs here rather than in the API because the API mounts that volume
    read-only, so every delete failed with EROFS. The worker lanes are the only
    processes with write access to it.

    The path is resolved here too, not passed in: the same volume is mounted at a
    different path in each container, so an API-supplied path would be wrong, and
    a delete sink that trusts a caller-supplied path is worth not building.

    Returns a status dict rather than raising, because Celery's JSON serialiser
    would not carry the distinction between "not found" and "refused" across the
    boundary intact.
    """
    from backend.preload_models import delete_model

    try:
        deleted = delete_model(model_name, whisper_model_size=variant)
    except ValueError as e:
        # Refused by policy, not by accident: a bundled asset, or a model
        # that is not Nojoin's own file or repo in its model cache.
        return {"status": "forbidden", "message": str(e)}
    except Exception as e:  # noqa: BLE001 -- boundary: the task answers with a status dict; a raised error would not serialise the distinction
        logger.error("Failed to delete model %s: %s", model_name, e, exc_info=True)
        return {"status": "error", "message": str(e)}

    if not deleted:
        return {"status": "not_found", "message": f"Model {model_name} not found."}

    logger.info("Deleted model %s (variant=%s)", model_name, variant)
    return {"status": "deleted", "message": f"Model {model_name} deleted."}


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
    except Exception as e:  # noqa: BLE001 -- boundary: a device probe reports what it can and must never fail
        return {"device": "unknown", "error": str(e)}


@celery_app.task(name="backend.worker.tasks.create_backup_task", bind=True)
def create_backup_task(
    self, include_audio: bool = True, archive_quality: str = "compressed"
):
    """
    Produce a current backup archive, reusing the cached one if nothing has changed.

    The archive itself is described by the cache manifest the API reads, so the result
    only names it.
    """
    import time

    from backend.core.backup import BackupManager
    from backend.core.backup.export_job import release_job_sync

    def report(stage: str, current: int = 0, total: int = 0) -> None:
        # Compressing a large library takes hours. Per-file reports let the UI show
        # progress, and their timestamps are how the API tells a slow export from one
        # whose worker died.
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
                "updated_at": time.time(),
            },
        )

    try:
        logger.info(
            "Starting backup task (include_audio=%s, archive_quality=%s)",
            include_audio,
            archive_quality,
        )
        report("Starting backup")

        entry, reused = BackupManager.export_backup(
            export_id=self.request.id,
            include_audio=include_audio,
            archive_quality=archive_quality,
            progress_callback=report,
        )

        logger.info(
            "Backup %s ready (%s)", entry.export_id, "reused" if reused else "built"
        )
        return {"status": "success", "export_id": entry.export_id, "reused": reused}

    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise e
    finally:
        release_job_sync(self.request.id)


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

        audio_path = confined_recording_path(recording.audio_path)
        if not audio_path or not os.path.exists(audio_path):
            logger.error(f"Audio file not found for recording {recording_id}")
            return

        base_path, _ = os.path.splitext(audio_path)
        proxy_path = f"{base_path}.mp3"

        if _paths_point_to_same_media(audio_path, proxy_path):
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
        if convert_to_proxy_mp3(audio_path, proxy_path, mix_to_mono=mix_to_mono):
            recording.proxy_path = proxy_path
            session.add(recording)
            session.commit()
            logger.info(f"Proxy generated successfully for recording {recording_id}")
        else:
            logger.error(f"Failed to generate proxy for recording {recording_id}")

    except Exception as e:  # noqa: BLE001 -- boundary: the proxy is optional; the original audio still plays
        logger.error(f"Error in generate_proxy_task for recording {recording_id}: {e}")
        # Not re-raised because proxy generation is optional/secondary.


@celery_app.task(name="backend.worker.tasks.cleanup_backup_artifacts", bind=True)
def cleanup_backup_artifacts(self, max_age_hours: int = 24):
    """
    Reclaim backup working files.

    Exported archives, uploaded archives, abandoned multipart uploads, and restore
    staging left by a worker that died mid-restore.

    The cached export is kept. Other exports, left by a build that died or replaced by
    a newer one, age out on a TTL.
    """
    from backend.core.backup import RESTORE_STAGING_DIRNAME, runtime
    from backend.core.backup import cache as backup_cache
    from backend.utils.path_manager import PathManager

    path_manager = PathManager()
    reclaimed = 0

    export_dir = runtime.backup_export_directory(path_manager)
    # The cached archive is kept until a newer export replaces it; everything else in
    # the export directory is an abandoned or superseded build.
    reclaimed += path_manager.cleanup_temp_files(
        export_dir,
        max_age_hours=max_age_hours,
        keep=backup_cache.protected_names(export_dir),
    )

    targets = [
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
        except Exception as e:  # noqa: BLE001 -- boundary: the restore lock also expires on its TTL, so a failed release is not fatal
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
    except Exception as e:  # noqa: BLE001 -- boundary: one failed rebuild must not stop the rest of the restore finalisation
        session.rollback()
        logger.error(
            "Canonical backfill failed for restored recording %s: %s", recording_id, e
        )

    # 2. Rebuild the RAG index for the transcript and every attached document.
    # Re-index, do not re-parse: document_pages is archived, so the parsed text
    # comes back with the backup. Re-running the parser would spend a vision
    # call per page to reproduce content the archive already restored.
    try:
        celery_app.send_task(
            "backend.worker.tasks.rebuild_recording_index_task", args=[recording_id]
        )
    except Exception as e:  # noqa: BLE001 -- boundary: a failed enqueue is logged; the index sweep picks the recording up later
        logger.error(
            "Failed to queue indexing for restored recording %s: %s", recording_id, e
        )

    # 3. Playback proxy, on the cpu lane where ffmpeg lives.
    if needs_proxy:
        try:
            celery_app.send_task(
                "backend.worker.tasks.generate_proxy_task", args=[recording_id]
            )
        except Exception as e:  # noqa: BLE001 -- boundary: the proxy is optional; the original audio still plays
            logger.error(
                "Failed to queue proxy generation for restored recording %s: %s",
                recording_id,
                e,
            )


@celery_app.task(
    name="backend.worker.tasks.send_telemetry_ping_task", base=DatabaseTask, bind=True
)
def send_telemetry_ping_task(self):
    """Send the anonymous telemetry ping, if the install consents.

    Best-effort throughout: a disabled install, an install still inside its
    consent grace period, an unreachable endpoint, or a rejected payload all
    return quietly. Nothing here retries, so a network fault cannot escalate
    into repeated calls against our own endpoint; the next six-hourly beat tick
    is the only retry. See backend/utils/telemetry.py for the consent rules and
    for why the cadence is six hours rather than a day.
    """
    from backend.utils import telemetry

    if not telemetry.should_send():
        logger.debug("Telemetry ping skipped: disabled or awaiting consent.")
        return

    try:
        payload = telemetry.build_payload(self.session)
    except Exception as exc:  # noqa: BLE001 -- boundary: telemetry must never break the worker
        logger.debug("Telemetry payload could not be built: %s", exc)
        return

    result = telemetry.send_payload(payload)
    # Recorded either way. A failure that leaves no trace is why an admin
    # staring at "Last sent: Never" cannot tell a silent install from a broken
    # one; Settings reads this to say which it is.
    telemetry.record_attempt(datetime.now(UTC), result)

    if result.ok:
        logger.info("Telemetry ping sent.")


@celery_app.task(name="backend.worker.tasks.send_telemetry_heartbeat_task")
def send_telemetry_heartbeat_task():
    """Send the install heartbeat, if one is due.

    Runs every fifteen minutes and returns at once unless this install has never
    sent one, has started on a new version, or has not sent one today, so a new
    install is counted within a quarter of an hour of starting. It needs no
    consent and no database: it carries only the install id and the version.
    The two exemptions and the retry spacing live in heartbeat_due(), in
    backend/utils/telemetry.py.
    """
    from backend.utils import telemetry

    now = datetime.now(UTC)
    if not telemetry.heartbeat_due(now):
        return

    payload = telemetry.build_heartbeat()
    result = telemetry.send_heartbeat(payload)
    telemetry.record_heartbeat_attempt(now, result, payload["version"])

    if result.ok:
        # Info rather than debug, so an operator reading the logs can see the
        # one outbound call they cannot switch off in the app, and where it went.
        logger.info(
            "Telemetry heartbeat sent to %s (see docs/TELEMETRY.md).",
            telemetry.heartbeat_endpoint(),
        )


__all__ = [name for name in globals() if not name.startswith("__")]
