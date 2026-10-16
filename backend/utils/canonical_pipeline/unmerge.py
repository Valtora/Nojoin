"""Undoing a speaker merge made by processing.

A merge rewrites each utterance's ``speaker_label`` to the survivor's label,
but ``diarization_label`` keeps the cluster the diariser gave the span. That
is what lets a merged cluster be restored as a speaker of its own, with
exactly the lines the diariser gave it, less any a person has since assigned
by hand.
"""

from dataclasses import dataclass

from .constants import *
from .speaker import (
    _append_speaker_correction_event,
    _resolve_active_recording_speaker,
    ensure_recording_speaker_aliases_for_speaker,
    refresh_recording_speaker_usage_state,
)

_PLACEHOLDER_SPEAKER_NUMBER = re.compile(r"^Speaker (\d+)$", re.IGNORECASE)


def _restorable_utterances(
    utterances: Sequence[TranscriptUtterance],
    *,
    diarization_label: str,
    speaker_id: int | None,
) -> list[TranscriptUtterance]:
    """The lines a merged cluster would get back: its own label, not hand-set.

    A line a person assigned is theirs whatever the diariser said, so a manual
    speaker lock keeps it where it is.
    """
    return [
        utterance
        for utterance in utterances
        if utterance.diarization_label == diarization_label
        and not utterance.manual_speaker_locked
        and utterance.recording_speaker_id != speaker_id
    ]


def _placeholder_name_for_restored_speaker(
    restored: RecordingSpeaker,
    speakers: Sequence[RecordingSpeaker],
) -> str:
    """Keep the row's own "Speaker N" when no other speaker shows it."""
    taken: set[str] = set()
    highest = 0
    for speaker in speakers:
        if speaker.id == restored.id or speaker.merged_into_id:
            continue
        for value in (speaker.name, speaker.local_name):
            text = str(value or "").strip()
            match = _PLACEHOLDER_SPEAKER_NUMBER.match(text)
            if match:
                taken.add(text.casefold())
                highest = max(highest, int(match.group(1)))
    own = str(restored.name or "").strip()
    if _PLACEHOLDER_SPEAKER_NUMBER.match(own) and own.casefold() not in taken:
        return own
    return f"Speaker {highest + 1}"


def list_merged_recording_speakers(session, recording_id: int) -> list[dict[str, Any]]:
    """Every merged cluster that an unmerge could still give lines back to.

    A cluster whose lines were all assigned by hand, or whose lines carry no
    diarisation label because they predate the column, has nothing to restore
    and is left out.
    """
    from .core import list_active_utterances

    speakers = _load_recording_speakers(session, recording_id)
    utterances = list_active_utterances(session, recording_id)
    rows: list[dict[str, Any]] = []
    for speaker in speakers:
        if not speaker.merged_into_id:
            continue
        survivor = _resolve_active_recording_speaker(session, speaker)
        restorable = _restorable_utterances(
            utterances,
            diarization_label=speaker.diarization_label,
            speaker_id=speaker.id,
        )
        if not restorable:
            continue
        rows.append(
            {
                "id": int(speaker.id),
                "public_id": speaker.public_id,
                "diarization_label": speaker.diarization_label,
                "name": speaker.name,
                "merged_into_id": int(survivor.id),
                "merged_into_label": survivor.diarization_label,
                "restorable_utterance_count": len(restorable),
                "has_voiceprint": bool(speaker.embedding),
            }
        )
    return rows


def _restore_merged_speaker_row(
    session,
    *,
    restored: RecordingSpeaker,
    speakers: Sequence[RecordingSpeaker],
    moving: Sequence[TranscriptUtterance],
) -> None:
    """Bring a merged row back as an unnamed, unlinked speaker.

    The merge put it under someone else's identity, and undoing the merge says
    that identity was not its own.
    """
    restored.merged_into_id = None
    restored.speaker_status = "active"
    restored.global_speaker_id = None
    restored.local_name = None
    restored.identity_confidence = None
    restored.identity_locked = False
    restored.name_last_edit_source = None
    restored.name = _placeholder_name_for_restored_speaker(restored, speakers)
    restored.first_seen_ms = min(utterance.start_ms for utterance in moving)
    restored.last_seen_ms = max(utterance.end_ms for utterance in moving)
    session.add(restored)
    session.flush()
    ensure_recording_speaker_aliases_for_speaker(session, restored)


@dataclass(frozen=True)
class _Unmerge:
    """One unmerge in progress: who gets the lines, who loses them, who asked."""

    restored: RecordingSpeaker
    survivor: RecordingSpeaker
    actor_user_id: int | None
    source: str


def _move_utterances_to_restored_speaker(
    session,
    *,
    moving: Sequence[TranscriptUtterance],
    projection_segments: list[dict[str, Any]],
    unmerge: _Unmerge,
) -> None:
    """Point each line at the restored speaker, in the rows and the projection.

    The lines keep their pipeline provenance rather than a manual lock: the
    diariser's attribution is being restored, not replaced.
    """
    restored = unmerge.restored
    index_by_public_id = {
        str(segment.get("id")): index
        for index, segment in enumerate(projection_segments)
        if segment.get("id")
    }
    for utterance in moving:
        old_label = utterance.speaker_label or UNKNOWN_SPEAKER
        old_values = {
            "speaker_label": old_label,
            "recording_speaker_id": utterance.recording_speaker_id,
            "revision": utterance.revision,
        }
        utterance.recording_speaker_id = restored.id
        utterance.speaker_label = restored.diarization_label
        utterance.revision += 1
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            actor_user_id=unmerge.actor_user_id,
            event_type="update_speaker",
            source=unmerge.source,
            old_values=old_values,
            new_values={
                "speaker_label": restored.diarization_label,
                "recording_speaker_id": restored.id,
                "unmerged_from_recording_speaker_id": unmerge.survivor.id,
            },
            resulting_revision=utterance.revision,
        )
        projection_index = index_by_public_id.get(utterance.public_id)
        if projection_index is None:
            continue
        reconcile_segment_assignment(
            projection_segments,
            projection_index,
            old_label,
            restored.diarization_label,
        )
        projection_segments[projection_index]["recording_speaker_id"] = restored.id
        projection_segments[projection_index]["revision"] = utterance.revision
        projection_segments[projection_index]["updated_at"] = (
            utterance.updated_at.isoformat()
        )


def unmerge_recording_speaker(
    session,
    *,
    recording_id: int,
    diarization_label: str,
    actor_user_id: int | None = None,
    source: str = "api",
) -> tuple[RecordingSpeaker, RecordingSpeaker, int]:
    """Give a merged cluster back its lines as a speaker of its own.

    Returns the restored speaker, the speaker it was merged into, and how many
    lines moved. Raises ``LookupError`` for an unknown label and ``ValueError``
    when the label is not merged or has no lines left to restore.
    """
    from .core import (
        list_active_utterances,
        recording_ready_for_canonical_backfill,
    )
    from .startup import ensure_canonical_backfill

    recording = session.get(Recording, recording_id)
    if recording is None:
        raise LookupError("Recording not found")

    if recording_ready_for_canonical_backfill(recording.status):
        ensure_canonical_backfill(session, recording_id)

    speakers = _load_recording_speakers(session, recording_id)
    restored = next(
        (
            speaker
            for speaker in speakers
            if speaker.diarization_label == diarization_label
        ),
        None,
    )
    if restored is None:
        raise LookupError(f"Speaker '{diarization_label}' not found")
    if not restored.merged_into_id:
        raise ValueError(
            f"Speaker '{diarization_label}' is not merged into another speaker"
        )

    survivor = _resolve_active_recording_speaker(session, restored)
    utterances = list_active_utterances(session, recording_id)
    moving = _restorable_utterances(
        utterances,
        diarization_label=diarization_label,
        speaker_id=restored.id,
    )
    if not moving:
        raise ValueError(
            f"No lines carry the label '{diarization_label}' that are not assigned by hand"
        )

    _restore_merged_speaker_row(
        session, restored=restored, speakers=speakers, moving=moving
    )

    transcript = _load_transcript(session, recording_id)
    projection_segments = [
        dict(segment)
        for segment in ((transcript.segments if transcript else None) or [])
    ]
    _move_utterances_to_restored_speaker(
        session,
        moving=moving,
        projection_segments=projection_segments,
        unmerge=_Unmerge(
            restored=restored,
            survivor=survivor,
            actor_user_id=actor_user_id,
            source=source,
        ),
    )

    # The survivor's span shrinks to the lines it still holds.
    remaining = [
        utterance
        for utterance in utterances
        if utterance.recording_speaker_id == survivor.id
    ]
    if remaining:
        survivor.first_seen_ms = min(utterance.start_ms for utterance in remaining)
        survivor.last_seen_ms = max(utterance.end_ms for utterance in remaining)
        session.add(survivor)

    _append_speaker_correction_event(
        session,
        recording_id=recording_id,
        actor_user_id=actor_user_id,
        source_recording_speaker_id=survivor.id,
        target_recording_speaker_id=restored.id,
        target_global_speaker_id=None,
        event_type=SpeakerCorrectionEventType.UNMERGE_SPEAKERS,
        scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
        effective_from_ms=restored.first_seen_ms,
        payload={
            "source": source,
            "new_speaker_name": restored.name,
            "diarization_label": restored.diarization_label,
            "target_public_id": restored.public_id,
            "unmerged_from_label": survivor.diarization_label,
            "unmerged_from_public_id": survivor.public_id,
            "utterance_count": len(moving),
        },
        update_source_provenance=True,
    )

    if transcript is not None:
        transcript.segments = projection_segments
        flag_modified(transcript, "segments")
        session.add(transcript)
    refresh_recording_speaker_usage_state(session, recording_id)

    return restored, survivor, len(moving)


__all__ = [name for name in globals() if not name.startswith("__")]
