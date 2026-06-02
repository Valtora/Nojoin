from .constants import *

@celery_app.task(name="backend.worker.tasks.cleanup_temp_recordings", base=DatabaseTask, bind=True)
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
def download_models_task(self, hf_token: str | None = None, whisper_model_size: str | None = None):
    """
    Task to download models in the background.
    Checks for existing downloads from preload_models.py and forwards that progress.
    """
    from backend.preload_models import download_models, check_model_status
    from backend.utils.download_progress import (
        get_download_progress,
        is_download_in_progress,
        is_download_complete,
        set_download_progress
    )
    
    # Reload config to ensure we have the latest settings
    config_manager.reload()
    
    # Check if models are already fully downloaded
    model_status = check_model_status(whisper_model_size=whisper_model_size or "turbo")
    all_downloaded = (
        model_status.get("whisper", {}).get("downloaded", False) and
        model_status.get("parakeet", {}).get("downloaded", False) and
        model_status.get("canary", {}).get("downloaded", False) and
        model_status.get("pyannote", {}).get("downloaded", False) and
        model_status.get("embedding", {}).get("downloaded", False)
    )
    
    if all_downloaded:
        logger.info("All models already downloaded, skipping download.")
        self.update_state(state='PROCESSING', meta={
            'progress': 100,
            'message': 'All models already downloaded!'
        })
        set_download_progress(100, "All models already downloaded!", status="complete")
        return {"status": "success", "message": "All models already downloaded."}
    
    # Check if there's an active download from preload_models.py
    # If so, poll and forward that progress until it completes
    if is_download_in_progress():
        logger.info("Download already in progress (from preload_models.py), forwarding progress...")
        while True:
            progress = get_download_progress()
            if progress is None:
                break
            
            status = progress.get("status", "downloading")
            if status == "complete":
                self.update_state(state='PROCESSING', meta={
                    'progress': 100,
                    'message': 'All models downloaded!'
                })
                return {"status": "success", "message": "All models downloaded successfully."}
            elif status == "error":
                error_msg = progress.get("message", "Download failed")
                raise Exception(error_msg)
            else:
                # Forward the progress to the Celery task state
                meta = {
                    'progress': progress.get('progress', 0),
                    'message': progress.get('message', 'Downloading...'),
                    'stage': progress.get('stage')
                }
                if progress.get('speed'):
                    meta['speed'] = progress['speed']
                if progress.get('eta'):
                    meta['eta'] = progress['eta']
                self.update_state(state='PROCESSING', meta=meta)
            
            time.sleep(1)
    
    # No active download, proceed with our own download
    def progress_callback(msg, percent, speed=None, eta=None, stage=None):
        meta = {'progress': percent, 'message': msg, 'stage': stage}
        if speed:
            meta['speed'] = speed
        if eta:
            meta['eta'] = eta
        self.update_state(state='PROCESSING', meta=meta)
    
    try:
        download_models(progress_callback=progress_callback, hf_token=hf_token, whisper_model_size=whisper_model_size)
        return {"status": "success", "message": "All models downloaded successfully."}
    except Exception as e:
        logger.error(f"Model download failed: {e}", exc_info=True)
        raise e


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
            "torch_version": torch.__version__
        }
    except ImportError:
        return {"device": "cpu", "error": "torch not installed"}
    except Exception as e:  # noqa: BLE001
        return {"device": "unknown", "error": str(e)}


@celery_app.task(name="backend.worker.tasks.create_backup_task", bind=True)
def create_backup_task(self, include_audio: bool = True):
    """
    Background task to create a backup zip file.
    Returns the path to the backup file.
    """
    from backend.core.backup_manager import BackupManager
    
    try:
        logger.info(f"Starting backup task (include_audio={include_audio})")
        self.update_state(state='PROCESSING', meta={'status': 'Creating backup...'})
        
        zip_path = BackupManager.create_backup_blocking(include_audio=include_audio)
        
        logger.info(f"Backup created successfully at {zip_path}")
        return {"status": "success", "zip_path": zip_path}
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise e



@celery_app.task(name="backend.worker.tasks.generate_proxy_task", base=DatabaseTask, bind=True)
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
        if convert_to_proxy_mp3(recording.audio_path, proxy_path, mix_to_mono=mix_to_mono):
            recording.proxy_path = proxy_path
            session.add(recording)
            session.commit()
            logger.info(f"Proxy generated successfully for recording {recording_id}")
        else:
            logger.error(f"Failed to generate proxy for recording {recording_id}")

    except Exception as e:  # noqa: BLE001
        logger.error(f"Error in generate_proxy_task for recording {recording_id}: {e}")
        # Not re-raised because proxy generation is optional/secondary.



__all__ = [name for name in globals() if not name.startswith('__')]
