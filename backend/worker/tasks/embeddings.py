from backend.utils.embedding_audio import select_recording_audio_for_embedding

from .constants import *


@celery_app.task(
    name="backend.worker.tasks.update_speaker_embedding_task",
    base=DatabaseTask,
    bind=True,
)
def update_speaker_embedding_task(
    self, recording_id: int, start: float, end: float, recording_speaker_id: int
):
    """
    Update the speaker embedding for a specific segment (Active Learning).
    """
    from backend.processing.embedding import merge_embeddings
    from backend.processing.embedding_core import extract_embedding_for_segments

    session = self.session
    try:
        recording = session.get(Recording, recording_id)

        target_audio = select_recording_audio_for_embedding(recording)
        if not target_audio:
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
            target_audio, [(start, end)], device_str=device
        )

        if new_embedding:
            from backend.processing.embedding import embedding_version_of
            from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION

            def _blend(row, incoming):
                """Merge in the new vector, or replace a stale-version one.

                Blending across extraction versions would average two unrelated
                regions of the vector space and produce a voiceprint that
                matches neither, so a version mismatch replaces outright.
                """
                current = row.embedding if row.embedding is not None else []
                if current and embedding_version_of(row) != EMBEDDING_METHOD_VERSION:
                    row.embedding = incoming
                else:
                    row.embedding = merge_embeddings(current, incoming, alpha=0.5)
                row.embedding_version = EMBEDDING_METHOD_VERSION
                session.add(row)

            _blend(target_recording_speaker, new_embedding)

            if target_recording_speaker.global_speaker_id:
                gs = session.get(
                    GlobalSpeaker, target_recording_speaker.global_speaker_id
                )
                if gs:
                    _blend(gs, new_embedding)

            session.commit()
            logger.info(
                f"Updated embedding for speaker {target_recording_speaker.diarization_label}"
            )
        else:
            logger.warning("Failed to extract embedding for update.")

    except Exception as e:
        logger.error(f"Failed to update speaker embedding: {e}", exc_info=True)
        session.rollback()


@celery_app.task(name="backend.worker.tasks.extract_embedding_task", bind=True)
def extract_embedding_task(
    self, audio_path: str, segments: list, device_str: str = "cpu", hf_token: str = None
):
    """
    Extract embedding from segments. Used by API for synchronous-like operations.
    """
    from backend.processing.embedding_core import extract_embedding_for_segments

    try:
        # If token not passed, try to get from config in worker
        if not hf_token:
            from backend.utils.config_manager import config_manager

            hf_token = config_manager.get("hf_token")

        return extract_embedding_for_segments(
            audio_path, segments, device_str, hf_token
        )
    except Exception as e:
        logger.error(f"Failed to extract embedding task: {e}", exc_info=True)
        return None


@celery_app.task(name="backend.worker.tasks.get_text_embedding_task")
def get_text_embedding_task(texts):
    """
    Generate text embeddings using fastembed. Offloads heavy inference from API.
    """
    from typing import List, Union

    from backend.processing.text_embedding import get_text_embedding_service

    try:
        embedding_service = get_text_embedding_service()
        return embedding_service.embed(texts)
    except Exception as e:
        logger.error(f"Failed to generate text embedding on worker: {e}", exc_info=True)
        return []


__all__ = [name for name in globals() if not name.startswith("__")]
