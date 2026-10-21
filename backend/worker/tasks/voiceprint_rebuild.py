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
left stale so a later run can retry them. So is a person whose re-extracted
speakers all hold too little speech to found a voiceprint: theirs is kept until a
recording that qualifies is linked to them (see ``_rebuild_people``).
"""

from enum import Enum

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
    speaker.embedding_speech_seconds = None
    session.add(speaker)


def _unit_vectors(speakers) -> list:
    """The finite, non-zero voiceprints of ``speakers``, scaled to unit length."""
    units = []
    for speaker in speakers:
        vector = np.asarray(speaker.embedding, dtype=float)
        if vector.ndim != 1 or not np.all(np.isfinite(vector)):
            continue
        norm = np.linalg.norm(vector)
        if norm:
            units.append(vector / norm)
    return units


def _unit_mean(units: list):
    """The unit-length mean of unit vectors, or ``None`` when they cancel out."""
    mean = np.mean(np.array(units), axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm else None


class _PersonRebuild(Enum):
    """What ``_rebuild_global_speaker`` did with one person."""

    REBUILT = "rebuilt"
    # Current-version voiceprints are linked to the person, but none of them
    # may found theirs.
    HELD_BACK = "held_back"
    # No usable current-version voiceprint is linked to the person.
    NOTHING_CURRENT = "nothing_current"


def _rebuild_global_speaker(
    session, global_speaker, method_version: int
) -> _PersonRebuild:
    """Recompute a person's voiceprint from their linked current-version ones.

    Only links that could found or join the voiceprint on any other path are
    averaged. Each recording's own voiceprint must meet
    ``LIBRARY_VOICEPRINT_MIN_SPEECH_S``, since a mean of short voiceprints is
    not what that floor measured (an unmeasured one is grandfathered). The
    person is changed only when they are rebuilt; what becomes of a stale
    voiceprint that is not is ``_rebuild_people``'s decision.
    """
    linked = session.exec(
        select(RecordingSpeaker)
        .where(RecordingSpeaker.global_speaker_id == global_speaker.id)
        .where(RecordingSpeaker.merged_into_id.is_(None))
    ).all()

    from backend.processing.embedding import (
        embedding_version_of,
        meets_library_evidence_floor,
    )

    current = [
        s for s in linked if s.embedding and embedding_version_of(s) == method_version
    ]
    if not _unit_vectors(current):
        return _PersonRebuild.NOTHING_CURRENT
    units = _unit_vectors(
        s for s in current if meets_library_evidence_floor(s.embedding_speech_seconds)
    )
    mean = _unit_mean(units) if units else None
    if mean is None:
        return _PersonRebuild.HELD_BACK

    global_speaker.embedding = mean.tolist()
    global_speaker.embedding_version = method_version
    session.add(global_speaker)
    return _PersonRebuild.REBUILT


def _stale_speakers_by_recording(
    session, method_version: int, user_id: int | None = None
) -> dict[int, list]:
    """Group every stale recording speaker by the recording that owns it.

    Scoping to the requesting user happens here rather than while iterating, so
    that the reported stale count describes that user's library and the run
    limit is spent on their recordings instead of on rows another user owns.

    A current-version voiceprint with no speech figure is not stale and is left
    alone. It predates the figure and is grandfathered past the evidence floor
    (see ``meets_library_evidence_floor``) until a path that re-extracts it
    anyway measures it, so this task never spends audio reads on it.
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


def _rebuild_recording_speakers(
    session, *, audio_path: str, speakers: list, ranges: dict, device: str
) -> tuple[int, int, int, set[int]]:
    """Re-extract one recording's stale speakers.

    Returns ``(rebuilt, cleared, failed, affected person ids)``.
    """
    from backend.processing.embedding_core import (
        EMBEDDING_METHOD_VERSION,
        extract_embedding_for_segments,
    )

    rebuilt = 0
    cleared = 0
    failed = 0
    affected: set[int] = set()

    for speaker in speakers:
        segments = ranges.get(int(speaker.id)) or []
        if not segments:
            # No utterance and no transcript segment names this speaker, so
            # there is no audio to re-extract from -- now or on any later run.
            _clear_dead_voiceprint(speaker, session, "no attributable speech")
            cleared += 1
            continue

        try:
            voiceprint = extract_embedding_for_segments(
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
            failed += 1
            continue

        if not voiceprint:
            # Extraction ran and produced nothing usable from these segments.
            # That is deterministic for this input, so retrying cannot help.
            _clear_dead_voiceprint(
                speaker, session, "extraction produced no usable vector"
            )
            cleared += 1
            continue

        speaker.embedding = voiceprint.embedding
        speaker.embedding_version = EMBEDDING_METHOD_VERSION
        speaker.embedding_speech_seconds = voiceprint.speech_seconds
        session.add(speaker)
        rebuilt += 1
        if speaker.global_speaker_id:
            affected.add(int(speaker.global_speaker_id))

    return rebuilt, cleared, failed, affected


def _rebuild_people(
    session, *, user_id: int | None, affected_global_ids: set[int], method_version: int
) -> tuple[int, int]:
    """Recompute every person who is still stale, or whose speakers changed.

    A person whose voiceprint is already of this version is recomputed only
    when one of their speakers was just re-extracted, and never when it is
    locked: a locked voiceprint is the user's own (recalibrating locks it), and
    a current one has nothing to repair.

    A stale voiceprint that can't be rebuilt is cleared when no current-version
    voiceprint is linked to the person at all, and kept when some are but none
    holds enough speech to found one. Either way it matches nothing, since it
    is of another version; kept, it stays visible on the person instead of
    vanishing in a background run, and the first qualifying link replaces it.

    Returns ``(rebuilt, cleared)``.
    """
    from backend.processing.embedding import embedding_version_of

    query = select(GlobalSpeaker)
    if user_id is not None:
        query = query.where(GlobalSpeaker.user_id == user_id)

    rebuilt = 0
    cleared = 0
    for global_speaker in session.exec(query).all():
        is_current = embedding_version_of(global_speaker) == method_version
        if is_current and (
            global_speaker.id not in affected_global_ids
            or global_speaker.is_voiceprint_locked
        ):
            continue
        outcome = _rebuild_global_speaker(session, global_speaker, method_version)
        if outcome is _PersonRebuild.REBUILT:
            rebuilt += 1
            continue
        is_stale = (
            global_speaker.embedding
            and embedding_version_of(global_speaker) != method_version
        )
        if is_stale and outcome is _PersonRebuild.HELD_BACK:
            logger.info(
                "Keeping stale voiceprint for person %s (%s): no current-version "
                "speaker of theirs holds enough speech to found one",
                global_speaker.id,
                global_speaker.name,
            )
        elif is_stale:
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
