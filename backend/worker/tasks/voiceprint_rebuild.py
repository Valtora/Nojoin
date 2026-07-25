"""Re-extract stored voiceprints under the current extraction method.

Cosine similarity is only meaningful between two embeddings produced by the same
extraction method, so when the method version is bumped every stored voiceprint
is left unmatchable until it is rebuilt from audio. This task performs that
rebuild.

It is deliberately operator-triggered rather than automatic on upgrade: it reads
every affected recording's audio and runs the embedding model over it, which is
real work on hardware the user owns.
"""

import numpy as np

from .constants import *  # noqa: F403


def _speaker_time_ranges(session, recording_id: int) -> dict:
    """Map recording_speaker_id -> list of (start_s, end_s).

    Prefers the canonical utterance table and falls back to the transcript's
    segment blob, because an imported recording processed before canonical
    writes were enabled has only the latter.
    """
    from backend.models.pipeline import TranscriptUtterance

    ranges: dict[int, list[tuple[float, float]]] = {}

    rows = session.execute(
        select(
            TranscriptUtterance.recording_speaker_id,
            TranscriptUtterance.start_ms,
            TranscriptUtterance.end_ms,
        )
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.recording_speaker_id.isnot(None))
    ).all()
    for speaker_id, start_ms, end_ms in rows:
        if speaker_id is None or start_ms is None or end_ms is None:
            continue
        ranges.setdefault(int(speaker_id), []).append(
            (float(start_ms) / 1000.0, float(end_ms) / 1000.0)
        )

    if ranges:
        return ranges

    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if not transcript or not transcript.segments:
        return ranges

    speakers = session.exec(
        select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    ).all()
    by_label = {s.diarization_label: s.id for s in speakers if s.id is not None}

    for segment in transcript.segments:
        speaker_id = by_label.get(segment.get("speaker"))
        if speaker_id is None:
            continue
        start = segment.get("start")
        end = segment.get("end")
        if start is None or end is None:
            continue
        ranges.setdefault(int(speaker_id), []).append((float(start), float(end)))

    return ranges


def _rebuild_global_speaker(session, global_speaker, method_version: int) -> bool:
    """Recompute a person's voiceprint as the mean of their current-version ones."""
    linked = session.exec(
        select(RecordingSpeaker)
        .where(RecordingSpeaker.global_speaker_id == global_speaker.id)
        .where(RecordingSpeaker.merged_into_id.is_(None))
    ).all()

    from backend.processing.embedding import embedding_version_of

    vectors = [
        np.asarray(s.embedding, dtype=float)
        for s in linked
        if s.embedding and embedding_version_of(s) == method_version
    ]
    vectors = [v for v in vectors if v.ndim == 1 and np.all(np.isfinite(v))]
    if not vectors:
        return False

    units = []
    for vector in vectors:
        norm = np.linalg.norm(vector)
        if norm:
            units.append(vector / norm)
    if not units:
        return False

    mean = np.mean(np.array(units), axis=0)
    norm = np.linalg.norm(mean)
    if not norm:
        return False

    global_speaker.embedding = (mean / norm).tolist()
    global_speaker.embedding_version = method_version
    session.add(global_speaker)
    return True


def _stale_speakers_by_recording(session, method_version: int) -> dict[int, list]:
    """Group every stale recording speaker by the recording that owns it."""
    from backend.processing.embedding import embedding_version_of

    rows = session.exec(
        select(RecordingSpeaker)
        .where(RecordingSpeaker.embedding.isnot(None))
        .where(RecordingSpeaker.merged_into_id.is_(None))
    ).all()

    by_recording: dict[int, list] = {}
    for speaker in rows:
        if speaker.embedding and embedding_version_of(speaker) != method_version:
            by_recording.setdefault(int(speaker.recording_id), []).append(speaker)
    return by_recording


def _rebuild_recording_speakers(
    session, *, audio_path: str, speakers: list, ranges: dict, device: str
) -> tuple[int, set[int]]:
    """Re-extract one recording's stale speakers. Returns (count, person ids)."""
    from backend.processing.embedding_core import (
        EMBEDDING_METHOD_VERSION,
        extract_embedding_for_segments,
    )

    rebuilt = 0
    affected: set[int] = set()

    for speaker in speakers:
        segments = ranges.get(int(speaker.id)) or []
        if not segments:
            continue
        try:
            embedding = extract_embedding_for_segments(
                audio_path, segments, device_str=device
            )
        except Exception as e:  # noqa: BLE001 -- boundary: per-speaker best effort
            logger.warning(
                "Voiceprint rebuild failed for speaker %s: %s", speaker.id, e
            )
            continue

        if not embedding:
            continue

        speaker.embedding = embedding
        speaker.embedding_version = EMBEDDING_METHOD_VERSION
        session.add(speaker)
        rebuilt += 1
        if speaker.global_speaker_id:
            affected.add(int(speaker.global_speaker_id))

    return rebuilt, affected


def _rebuild_people(
    session, *, user_id: int | None, affected_global_ids: set[int], method_version: int
) -> int:
    """Recompute every person whose speakers changed or who is still stale."""
    from backend.processing.embedding import embedding_version_of

    query = select(GlobalSpeaker)
    if user_id is not None:
        query = query.where(GlobalSpeaker.user_id == user_id)

    rebuilt = 0
    for global_speaker in session.exec(query).all():
        already_current = (
            global_speaker.id not in affected_global_ids
            and embedding_version_of(global_speaker) == method_version
        )
        if already_current:
            continue
        if _rebuild_global_speaker(session, global_speaker, method_version):
            rebuilt += 1
    return rebuilt


@celery_app.task(
    name="backend.worker.tasks.rebuild_voiceprints_task",
    base=DatabaseTask,
    bind=True,
)
def rebuild_voiceprints_task(self, user_id: int | None = None, limit: int = 500):
    """Re-extract stale voiceprints and rebuild the people who depend on them.

    Args:
        user_id: Restrict to one user's recordings. ``None`` processes all.
        limit: Maximum number of recordings to touch in one run, so a large
            library degrades into repeated runs rather than one unbounded job.
    """
    from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION
    from backend.utils.embedding_audio import select_recording_audio_for_embedding

    session = self.session
    device = "cuda" if config_manager.get("use_gpu", True) else "cpu"

    by_recording = _stale_speakers_by_recording(session, EMBEDDING_METHOD_VERSION)
    stale_total = sum(len(v) for v in by_recording.values())

    rebuilt_speakers = 0
    skipped_recordings = 0
    touched_recordings = 0
    affected_global_ids: set[int] = set()

    for recording_id, speakers in list(by_recording.items())[:limit]:
        recording = session.get(Recording, recording_id)
        if recording is None or (user_id is not None and recording.user_id != user_id):
            continue

        audio_path = select_recording_audio_for_embedding(recording)
        if not audio_path:
            # Audio is gone (archived export, manual cleanup). Nothing to
            # rebuild from; the stale voiceprint simply stays unmatchable.
            skipped_recordings += 1
            continue

        touched_recordings += 1
        rebuilt, affected = _rebuild_recording_speakers(
            session,
            audio_path=audio_path,
            speakers=speakers,
            ranges=_speaker_time_ranges(session, recording_id),
            device=device,
        )
        rebuilt_speakers += rebuilt
        affected_global_ids |= affected
        session.commit()

    rebuilt_people = _rebuild_people(
        session,
        user_id=user_id,
        affected_global_ids=affected_global_ids,
        method_version=EMBEDDING_METHOD_VERSION,
    )
    session.commit()

    summary = {
        "method_version": EMBEDDING_METHOD_VERSION,
        "stale_speakers_found": stale_total,
        "recordings_processed": touched_recordings,
        "recordings_skipped_no_audio": skipped_recordings,
        "speakers_rebuilt": rebuilt_speakers,
        "people_rebuilt": rebuilt_people,
        "recordings_remaining": max(0, len(by_recording) - limit),
    }
    record_pipeline_metric(
        stage="voiceprint_rebuild",
        recording_id=None,
        payload=summary,
        log=logger,
    )
    logger.info("Voiceprint rebuild complete: %s", summary)
    return summary


__all__ = ["rebuild_voiceprints_task"]
