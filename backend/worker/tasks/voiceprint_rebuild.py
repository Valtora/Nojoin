"""Re-extract stored voiceprints under the current extraction method.

Cosine similarity is only meaningful between two embeddings produced by the same
extraction method, so when the method version is bumped every stored voiceprint
is left unmatchable until it is rebuilt from audio. This task performs that
rebuild.

It runs as scheduled background maintenance rather than on request. A stale
voiceprint degrades speaker identification silently, so requiring someone to
notice and ask for the repair means the feature stays broken for exactly as long
as nobody looks. Reading audio and running the embedding model is real work on
hardware the user owns, so each scheduled run is bounded to a limited number of
recordings (``AUTOMATIC_VOICEPRINT_REBUILD_LIMIT``): the sweep repeats, so a
large library converges over several runs instead of queueing one unbounded pile
of GPU work ahead of a live meeting.

A stale voiceprint that cannot be rebuilt is cleared rather than left in place.
Some speaker rows keep an embedding but no longer own any attributable speech --
re-diarisation can fold their segments into another speaker -- and others belong
to recordings whose audio has been removed. Neither can ever be re-extracted, so
leaving them stale makes the rebuild a permanent no-op and the "needs rebuilding"
prompt impossible to clear. An unmatchable voiceprint contributes nothing to
identification, so dropping it loses no capability and lets the run converge.
Transient extraction failures are deliberately excluded from that rule: they are
left stale so a later run can retry them.
"""

import numpy as np

from .constants import *  # noqa: F403


def _ranges_from_utterances(session, recording_id: int) -> dict:
    """Map recording_speaker_id -> [(start_s, end_s)] from the canonical table."""
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

    return ranges


def _ranges_from_transcript_segments(session, recording_id: int) -> dict:
    """Map recording_speaker_id -> [(start_s, end_s)] from the transcript blob."""
    ranges: dict[int, list[tuple[float, float]]] = {}

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


def _speaker_time_ranges(session, recording_id: int) -> dict:
    """Map recording_speaker_id -> list of (start_s, end_s).

    Prefers the canonical utterance table and fills the remaining speakers from
    the transcript's segment blob. The fallback is applied per speaker rather
    than per recording: a recording processed across a pipeline change can have
    utterance rows for some of its speakers and only transcript segments for the
    rest, and an all-or-nothing fallback leaves exactly those speakers with no
    audio to rebuild from.
    """
    ranges = _ranges_from_utterances(session, recording_id)

    for speaker_id, segments in _ranges_from_transcript_segments(
        session, recording_id
    ).items():
        if speaker_id not in ranges:
            ranges[speaker_id] = segments

    return ranges


def _clear_dead_voiceprint(speaker, session, reason: str) -> None:
    """Drop a stale voiceprint that can never be re-extracted.

    The speaker row itself is kept; only the unusable vector is removed.
    """
    logger.info(
        "Clearing unrebuildable voiceprint for recording speaker %s (%s): %s",
        speaker.id,
        getattr(speaker, "diarization_label", "?"),
        reason,
    )
    speaker.embedding = None
    speaker.embedding_version = None
    session.add(speaker)


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


def _stale_speakers_by_recording(
    session, method_version: int, user_id: int | None = None
) -> dict[int, list]:
    """Group every stale recording speaker by the recording that owns it.

    Scoping to the requesting user happens here rather than while iterating, so
    that the reported stale count describes that user's library and the run
    limit is spent on their recordings instead of on rows another user owns.
    """
    from backend.processing.embedding import embedding_version_of

    query = select(RecordingSpeaker)
    if user_id is not None:
        query = query.join(
            Recording, Recording.id == RecordingSpeaker.recording_id
        ).where(Recording.user_id == user_id)
    query = query.where(RecordingSpeaker.embedding.isnot(None)).where(
        RecordingSpeaker.merged_into_id.is_(None)
    )

    by_recording: dict[int, list] = {}
    for speaker in session.exec(query).all():
        # The truthiness check is not redundant with the SQL predicate: the
        # column is JSON, so a stored JSON ``null`` is not SQL NULL and passes
        # ``IS NOT NULL`` while carrying no vector.
        if speaker.embedding and embedding_version_of(speaker) != method_version:
            by_recording.setdefault(int(speaker.recording_id), []).append(speaker)
    return by_recording


def _rebuild_speaker(
    session, speaker, *, audio_path: str, segments: list, device: str
) -> str:
    """Re-extract one stale speaker: ``"rebuilt"``, ``"cleared"`` or ``"failed"``."""
    from backend.processing.embedding_core import (
        EMBEDDING_METHOD_VERSION,
        extract_embedding_for_segments,
    )

    try:
        embedding = extract_embedding_for_segments(
            audio_path, segments, device_str=device
        )
    except Exception as e:  # noqa: BLE001 -- boundary: per-speaker best effort
        # Left stale on purpose. An exception here can be transient (a
        # decode hiccup, a busy device), so a later run must be able to
        # retry rather than find the voiceprint already discarded.
        logger.warning(
            "Voiceprint rebuild failed for speaker %s, leaving it stale to retry: %s",
            speaker.id,
            e,
        )
        return "failed"

    if not embedding:
        # Extraction ran and produced nothing usable from these segments.
        # That is deterministic for this input, so retrying cannot help.
        _clear_dead_voiceprint(speaker, session, "extraction produced no usable vector")
        return "cleared"

    speaker.embedding = embedding
    speaker.embedding_version = EMBEDDING_METHOD_VERSION
    session.add(speaker)
    return "rebuilt"


def _rebuild_recording_speakers(
    session, *, audio_path: str, speakers: list, ranges: dict, device: str
) -> tuple[int, int, int, set[int]]:
    """Re-extract one recording's stale speakers.

    The recording is made croppable once (a media container is decoded to a
    temporary WAV) and every speaker is cropped from that, rather than each
    extraction decoding the whole recording again.

    Returns ``(rebuilt, cleared, failed, affected person ids)``.
    """
    from backend.core.exceptions import AudioFormatError
    from backend.utils.embedding_audio import pyannote_readable_audio

    outcomes = {"rebuilt": 0, "cleared": 0, "failed": 0}
    affected: set[int] = set()

    to_extract = []
    for speaker in speakers:
        segments = ranges.get(int(speaker.id)) or []
        if segments:
            to_extract.append((speaker, segments))
            continue
        # No utterance and no transcript segment names this speaker, so
        # there is no audio to re-extract from -- now or on any later run.
        _clear_dead_voiceprint(speaker, session, "no attributable speech")
        outcomes["cleared"] += 1

    if to_extract:
        try:
            with pyannote_readable_audio(audio_path) as readable_path:
                for speaker, segments in to_extract:
                    outcome = _rebuild_speaker(
                        session,
                        speaker,
                        audio_path=readable_path,
                        segments=segments,
                        device=device,
                    )
                    outcomes[outcome] += 1
                    if outcome == "rebuilt" and speaker.global_speaker_id:
                        affected.add(int(speaker.global_speaker_id))
        except AudioFormatError as e:
            # The decode itself failed, before any speaker was cropped. It can
            # be transient (a full temp directory), so every voiceprint is
            # left stale for a later run instead of being cleared.
            logger.warning(
                "Could not decode %s for voiceprint rebuild, leaving %d speaker(s) "
                "stale to retry: %s",
                audio_path,
                len(to_extract),
                e,
            )
            outcomes["failed"] += len(to_extract)

    return outcomes["rebuilt"], outcomes["cleared"], outcomes["failed"], affected


def _rebuild_people(
    session, *, user_id: int | None, affected_global_ids: set[int], method_version: int
) -> tuple[int, int]:
    """Recompute every person whose speakers changed or who is still stale.

    Returns ``(rebuilt, cleared)``.
    """
    from backend.processing.embedding import embedding_version_of

    query = select(GlobalSpeaker)
    if user_id is not None:
        query = query.where(GlobalSpeaker.user_id == user_id)

    rebuilt = 0
    cleared = 0
    for global_speaker in session.exec(query).all():
        already_current = (
            global_speaker.id not in affected_global_ids
            and embedding_version_of(global_speaker) == method_version
        )
        if already_current:
            continue
        if _rebuild_global_speaker(session, global_speaker, method_version):
            rebuilt += 1
            continue
        is_stale = (
            global_speaker.embedding
            and embedding_version_of(global_speaker) != method_version
        )
        if is_stale:
            # No current-version speaker remains to average, so this person's
            # voiceprint can never be recomputed. The person is kept; only the
            # unmatchable vector goes.
            logger.info(
                "Clearing unrebuildable voiceprint for person %s (%s): "
                "no current-version speakers remain",
                global_speaker.id,
                global_speaker.name,
            )
            global_speaker.embedding = None
            global_speaker.embedding_version = None
            session.add(global_speaker)
            cleared += 1
    return rebuilt, cleared


@celery_app.task(
    name="backend.worker.tasks.rebuild_voiceprints_task",
    base=DatabaseTask,
    bind=True,
)
def rebuild_voiceprints_task(self, user_id: int | None = None, limit: int = 500):
    """Re-extract stale voiceprints and rebuild the people who depend on them.

    Stale voiceprints that cannot be re-extracted are cleared, so that a run
    always leaves the library in a converged state rather than reporting success
    over work it silently could not do.

    Args:
        user_id: Restrict to one user's recordings. ``None`` processes all.
        limit: Maximum number of recordings to touch in one run, so a large
            library degrades into repeated runs rather than one unbounded job.
    """
    from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION
    from backend.utils.embedding_audio import select_recording_audio_for_embedding

    session = self.session
    device = "cuda" if config_manager.get("use_gpu", True) else "cpu"

    by_recording = _stale_speakers_by_recording(
        session, EMBEDDING_METHOD_VERSION, user_id=user_id
    )
    stale_total = sum(len(v) for v in by_recording.values())

    rebuilt_speakers = 0
    cleared_speakers = 0
    failed_speakers = 0
    skipped_recordings = 0
    missing_recordings = 0
    touched_recordings = 0
    affected_global_ids: set[int] = set()

    for recording_id, speakers in list(by_recording.items())[:limit]:
        recording = session.get(Recording, recording_id)
        if recording is None:
            # The owning recording is gone, so these speaker rows are orphans
            # with no audio behind them.
            missing_recordings += 1
            for speaker in speakers:
                _clear_dead_voiceprint(speaker, session, "recording no longer exists")
                cleared_speakers += 1
            session.commit()
            continue

        audio_path = select_recording_audio_for_embedding(recording)
        if not audio_path:
            # Audio is gone (archived export, manual cleanup). Nothing to
            # rebuild from, now or later, so the stale voiceprints are dropped.
            skipped_recordings += 1
            for speaker in speakers:
                _clear_dead_voiceprint(speaker, session, "recording audio unavailable")
                cleared_speakers += 1
            session.commit()
            continue

        touched_recordings += 1
        rebuilt, cleared, failed, affected = _rebuild_recording_speakers(
            session,
            audio_path=audio_path,
            speakers=speakers,
            ranges=_speaker_time_ranges(session, recording_id),
            device=device,
        )
        rebuilt_speakers += rebuilt
        cleared_speakers += cleared
        failed_speakers += failed
        affected_global_ids |= affected
        session.commit()

    rebuilt_people, cleared_people = _rebuild_people(
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
        "recordings_missing": missing_recordings,
        "speakers_rebuilt": rebuilt_speakers,
        "speakers_cleared_unrebuildable": cleared_speakers,
        "speakers_failed_retryable": failed_speakers,
        "people_rebuilt": rebuilt_people,
        "people_cleared_unrebuildable": cleared_people,
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
