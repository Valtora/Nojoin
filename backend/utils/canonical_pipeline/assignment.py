"""Speaker assignment targets, segment speakers, and utterance states."""

from backend.utils.live_speaker_labels import live_speaker_display_name

from .constants import *
from .speaker import *
from .speaker_matching import _find_matching_recording_speaker


def _live_assignment_target(session: Any, recording_id: int, label: str | None) -> bool:
    if live_speaker_display_name(label) is None:
        return False
    heard = session.execute(
        select(TranscriptUtterance.id)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.source_kind == "live")
        .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
        .where(TranscriptUtterance.speaker_label == label)
        .limit(1)
    ).first()
    if heard is None:
        raise LookupError("Live speaker not found in this recording")
    return True


def resolve_assignment_target(
    session,
    *,
    recording_id: int,
    recording: Recording,
    new_speaker_name: str,
    global_speaker_id: int | None,
    diarization_label: str | None,
    source_speaker: RecordingSpeaker | None = None,
    scope: SpeakerCorrectionScope = SpeakerCorrectionScope.UTTERANCE_ONLY,
) -> RecordingSpeaker | None:
    if _live_assignment_target(session, recording_id, diarization_label):
        return None

    cleaned_name = new_speaker_name.strip()
    recording_speakers = ensure_recording_speaker_aliases(session, recording_id)

    if diarization_label:
        recording_speaker = _find_matching_recording_speaker(
            session,
            recording_id=recording_id,
            recording_speakers=recording_speakers,
            value=diarization_label.strip(),
            source_run_id=None,
        )
        if recording_speaker is not None:
            return recording_speaker
        raise LookupError("Speaker not found in recording")

    if global_speaker_id is not None:
        global_speaker = session.execute(
            select(GlobalSpeaker)
            .where(GlobalSpeaker.id == global_speaker_id)
            .where(GlobalSpeaker.user_id == recording.user_id)
        ).scalar_one_or_none()
        if global_speaker is None:
            raise LookupError("Global speaker not found")

        # Merged rows keep their global_speaker_id but are hidden by
        # filter_recording_speakers_for_public_read, so returning one pins the
        # utterance to a speaker the transcript cannot render and the UI falls
        # back to the raw diarization label. Skipping them lands on the survivor
        # when it holds the same identity, and otherwise falls through to a
        # fresh manual row below. Following the merge chain would be wrong here:
        # it leads to whoever the row was merged into, not the person asked for.
        for recording_speaker in recording_speakers:
            if (
                recording_speaker.merged_into_id is None
                and recording_speaker.global_speaker_id == global_speaker.id
            ):
                return recording_speaker

        label = f"MANUAL_{uuid4().hex[:8]}"
        recording_speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label=label,
            global_speaker_id=global_speaker.id,
            name=None,
            speaker_kind="manual",
        )
        session.add(recording_speaker)
        session.flush()
        recording_speaker.global_speaker = global_speaker
        ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
        return recording_speaker

    recording_speaker = _find_matching_recording_speaker(
        session,
        recording_id=recording_id,
        recording_speakers=recording_speakers,
        value=cleaned_name,
        source_run_id=None,
    )
    if recording_speaker is not None:
        return recording_speaker

    if (
        source_speaker is not None
        and source_speaker.merged_into_id is None
        and scope
        in {
            SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD,
            SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
        }
        and str(source_speaker.diarization_label or "").startswith("LIVE_")
    ):
        source_speaker.global_speaker_id = None
        source_speaker.global_speaker = None
        source_speaker.local_name = cleaned_name
        source_speaker.name = None
        source_speaker.identity_confidence = 1.0
        source_speaker.identity_locked = True
        ensure_recording_speaker_aliases_for_speaker(session, source_speaker)
        session.add(source_speaker)
        return source_speaker

    label = f"MANUAL_{uuid4().hex[:8]}"
    recording_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=label,
        local_name=cleaned_name,
        name=None,
        speaker_kind="manual",
    )
    session.add(recording_speaker)
    session.flush()
    ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
    return recording_speaker


def _resolve_recording_speaker_for_value(
    session,
    *,
    recording_id: int,
    recording: Recording | None,
    speaker_value: str,
    recording_speakers: list[RecordingSpeaker],
    source_run_id: int | None,
    source_segment: dict[str, Any],
) -> RecordingSpeaker | None:
    cleaned_value = speaker_value.strip()
    if _names_no_speaker(cleaned_value):
        return None

    existing_speaker = _find_matching_recording_speaker(
        session,
        recording_id=recording_id,
        recording_speakers=recording_speakers,
        value=cleaned_value,
        source_run_id=source_run_id,
        segment_start_ms=_segment_to_ms(source_segment.get("start", 0.0)),
    )
    if existing_speaker is not None:
        return existing_speaker

    label = (
        cleaned_value
        if LABEL_PATTERN.match(cleaned_value)
        else f"MANUAL_{uuid4().hex[:8]}"
    )
    recording_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=label,
        local_name=None if LABEL_PATTERN.match(cleaned_value) else cleaned_value,
        name=None,
        speaker_kind=_speaker_kind_for_label(label, source_segment),
        speaker_status="active",
        processing_run_id=source_run_id,
    )
    session.add(recording_speaker)
    session.flush()
    recording_speakers.append(recording_speaker)
    ensure_recording_speaker_aliases_for_speaker(
        session,
        recording_speaker,
        source_run_id=source_run_id,
    )
    return recording_speaker


def _state_for_segment(
    recording: Recording, segment: dict[str, Any]
) -> TranscriptUtteranceState:
    if segment.get("provisional") is True:
        return TranscriptUtteranceState.PROVISIONAL
    status_value = (
        recording.status.value
        if isinstance(recording.status, RecordingStatus)
        else str(recording.status)
    )
    if status_value == RecordingStatus.PROCESSED.value:
        return TranscriptUtteranceState.FINALIZED
    return TranscriptUtteranceState.STABLE


__all__ = [
    "resolve_assignment_target",
    "_resolve_recording_speaker_for_value",
    "_state_for_segment",
]
