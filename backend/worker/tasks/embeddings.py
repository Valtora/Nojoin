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

        voiceprint = extract_embedding_for_segments(
            target_audio, [(start, end)], device_str=device
        )

        if voiceprint:
            gs = (
                session.get(GlobalSpeaker, target_recording_speaker.global_speaker_id)
                if target_recording_speaker.global_speaker_id
                else None
            )
            _learn_recording_speaker_voiceprint(
                target_recording_speaker, voiceprint, gs
            )
            session.add(target_recording_speaker)

            if gs:
                _learn_person_voiceprint(gs, voiceprint)
                session.add(gs)

            session.commit()
            logger.info(
                f"Updated embedding for speaker {target_recording_speaker.diarization_label}"
            )
        else:
            logger.warning("Failed to extract embedding for update.")

    except Exception as e:
        logger.error(f"Failed to update speaker embedding: {e}", exc_info=True)
        session.rollback()


def _learn_recording_speaker_voiceprint(row, voiceprint, person) -> None:
    """Blend one segment's voiceprint into a recording speaker's.

    The speaker's speech figure is never added to. It describes the extraction
    the voiceprint came from, and a blend is not one: adding let one 4.5 s
    segment moved back and forth four times lift a 2 s speaker to 20 s and
    found a library voiceprint from it. The figure changes only when the
    segment's voiceprint becomes the speaker's outright, because the speaker
    had none or only a stale one. A voiceprint the segment changes is judged
    again against ``person``, the person the speaker is linked to, before the
    segment is offered to their voiceprint (``judge_drift``).
    """
    from backend.processing.embedding import (
        VoiceprintBlend,
        blend_voiceprint,
        judge_drift,
    )

    blend = blend_voiceprint(row, voiceprint.embedding, voiceprint.version, alpha=0.5)
    if blend in (VoiceprintBlend.REFUSED, VoiceprintBlend.KEPT):
        return
    judge_drift(row, person)
    if blend in (VoiceprintBlend.COPIED, VoiceprintBlend.REPLACED):
        row.embedding_speech_seconds = voiceprint.speech_seconds


def _learn_person_voiceprint(person, voiceprint) -> None:
    """Offer one segment's voiceprint to the linked person's library voiceprint.

    A voiceprint from another extraction version cannot be blended with, so it
    is replaced, and a replacement founds a new voiceprint: like any other, it
    needs ``LIBRARY_VOICEPRINT_MIN_SPEECH_S`` of speech.

    A locked voiceprint is left as it is (``skipped_locked``): moving a segment
    corrects a transcript line, not a voiceprint the user curated and locked.
    """
    from backend.processing.embedding import update_library_voiceprint

    update = update_library_voiceprint(
        person, voiceprint, alpha=0.5, locked=bool(person.is_voiceprint_locked)
    )
    logger.info("Segment voiceprint for person %s: %s", person.id, update.status.value)


def _extract_segments_voiceprint(audio_path, segments, device_str, hf_token):
    """The voiceprint of ``segments``, or None when extraction fails."""
    from backend.processing.embedding_core import extract_embedding_for_segments

    try:
        if not hf_token:
            from backend.utils.config_manager import config_manager

            hf_token = config_manager.get("hf_token")

        return extract_embedding_for_segments(
            audio_path, segments, device_str, hf_token
        )
    except Exception as e:
        logger.error(f"Failed to extract embedding task: {e}", exc_info=True)
        return None


@celery_app.task(name="backend.worker.tasks.extract_embedding_task", bind=True)
def extract_embedding_task(
    self, audio_path: str, segments: list, device_str: str = "cpu", hf_token: str = None
):
    """
    Extract embedding from segments, returned as a bare list of floats.

    Kept with its original name and return shape for an API older than this
    worker during an upgrade; the current API calls
    ``extract_measured_embedding_task``, whose result also carries the speech
    the voiceprint was built from.
    """
    voiceprint = _extract_segments_voiceprint(
        audio_path, segments, device_str, hf_token
    )
    return voiceprint.embedding if voiceprint is not None else None


@celery_app.task(name="backend.worker.tasks.extract_measured_embedding_task", bind=True)
def extract_measured_embedding_task(
    self, audio_path: str, segments: list, device_str: str = "cpu", hf_token: str = None
):
    """
    Extract a voiceprint from segments with the speech it was built from.

    Returns ``RecordingVoiceprint.to_task_result()``: the embedding and the
    seconds of selected crop speech behind it. A new name rather than a new
    shape for ``extract_embedding_task``, so that an API and a worker of
    different versions never misread each other's results.
    """
    voiceprint = _extract_segments_voiceprint(
        audio_path, segments, device_str, hf_token
    )
    return voiceprint.to_task_result() if voiceprint is not None else None


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
