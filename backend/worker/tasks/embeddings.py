from .constants import *

@celery_app.task(name="backend.worker.tasks.update_speaker_embedding_task", base=DatabaseTask, bind=True)
def update_speaker_embedding_task(self, recording_id: int, start: float, end: float, recording_speaker_id: int):
    """
    Update the speaker embedding for a specific segment (Active Learning).
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    from backend.processing.embedding import merge_embeddings
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        
        target_audio = recording.audio_path
        if not target_audio or not os.path.exists(target_audio):
            if recording.proxy_path and os.path.exists(recording.proxy_path):
                target_audio = recording.proxy_path
            else:
                logger.warning(f"Recording {recording_id} not found or audio missing.")
                return

        target_recording_speaker = session.get(RecordingSpeaker, recording_speaker_id)
        if not target_recording_speaker:
            logger.warning(f"RecordingSpeaker {recording_speaker_id} not found.")
            return

        device = "cuda" if config_manager.get("use_gpu", True) else "cpu"
        
        # Extract embedding for this segment
        # Passes a list of segments [(start, end)] for embedding extraction.
        new_embedding = extract_embedding_for_segments(
            target_audio, 
            [(start, end)], 
            device_str=device
        )

        if new_embedding:
            # Merge into RecordingSpeaker
            current_emb = target_recording_speaker.embedding if target_recording_speaker.embedding is not None else []
            
            target_recording_speaker.embedding = merge_embeddings(
                current_emb, 
                new_embedding, 
                alpha=0.5
            )
            session.add(target_recording_speaker)
            
            # Merge into GlobalSpeaker
            if target_recording_speaker.global_speaker_id:
                gs = session.get(GlobalSpeaker, target_recording_speaker.global_speaker_id)
                if gs:
                    gs_emb = gs.embedding if gs.embedding is not None else []
                    gs.embedding = merge_embeddings(
                        gs_emb,
                        new_embedding,
                        alpha=0.5
                    )
                    session.add(gs)
            
            session.commit()
            logger.info(f"Updated embedding for speaker {target_recording_speaker.diarization_label}")
        else:
            logger.warning("Failed to extract embedding for update.")

    except Exception as e:
        logger.error(f"Failed to update speaker embedding: {e}", exc_info=True)
        session.rollback()


@celery_app.task(name="backend.worker.tasks.extract_embedding_task", bind=True)
def extract_embedding_task(self, audio_path: str, segments: list, device_str: str = "cpu", hf_token: str = None):
    """
    Extract embedding from segments. Used by API for synchronous-like operations.
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    try:
        # If token not passed, try to get from config in worker
        if not hf_token:
            from backend.utils.config_manager import config_manager
            hf_token = config_manager.get("hf_token")
            
        return extract_embedding_for_segments(audio_path, segments, device_str, hf_token)
    except Exception as e:
        logger.error(f"Failed to extract embedding task: {e}", exc_info=True)
        return None


@celery_app.task(name="backend.worker.tasks.get_text_embedding_task")
def get_text_embedding_task(texts):
    """
    Generate text embeddings using fastembed. Offloads heavy inference from API.
    """
    from backend.processing.text_embedding import get_text_embedding_service
    from typing import List, Union
    try:
        embedding_service = get_text_embedding_service()
        return embedding_service.embed(texts)
    except Exception as e:
        logger.error(f"Failed to generate text embedding on worker: {e}", exc_info=True)
        return []



__all__ = [name for name in globals() if not name.startswith('__')]
