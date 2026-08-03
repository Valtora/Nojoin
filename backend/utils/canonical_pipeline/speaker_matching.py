"""Resolving a speaker value onto an existing recording speaker.

Split from speaker.py, which is size-capped. Nothing in that module calls this
any more; core.py is the only consumer, so the dependency runs one way and the
matching rules live apart from the identity-mutation code that used to house
them.
"""

from .constants import *  # noqa: F403
from .speaker import (  # noqa: F401
    _apply_source_run_provenance,
    _ensure_recording_speaker_alias,
    _resolve_active_recording_speaker,
)


def _find_matching_recording_speaker(
    session,
    *,
    recording_id: int,
    recording_speakers: list[RecordingSpeaker],
    value: str,
    source_run_id: int | None,
    segment_start_ms: int | None = None,
) -> RecordingSpeaker | None:
    speaker_ids = [speaker.id for speaker in recording_speakers]
    if speaker_ids and segment_start_ms is not None:
        alias_rows = (
            session.execute(
                select(RecordingSpeakerAlias)
                .where(RecordingSpeakerAlias.recording_speaker_id.in_(speaker_ids))
                .where(RecordingSpeakerAlias.active.is_(True))
                .where(RecordingSpeakerAlias.alias_value == value)
                .where(
                    or_(
                        RecordingSpeakerAlias.valid_from_ms.is_(None),
                        RecordingSpeakerAlias.valid_from_ms <= segment_start_ms,
                    )
                )
                .where(
                    or_(
                        RecordingSpeakerAlias.valid_to_ms.is_(None),
                        RecordingSpeakerAlias.valid_to_ms > segment_start_ms,
                    )
                )
                .order_by(
                    func.coalesce(RecordingSpeakerAlias.valid_from_ms, -1).desc(),
                    RecordingSpeakerAlias.id.desc(),
                )
            )
            .scalars()
            .all()
        )
        speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}
        for alias_row in alias_rows:
            alias_speaker = speakers_by_id.get(alias_row.recording_speaker_id)
            if alias_speaker is None:
                alias_speaker = session.get(
                    RecordingSpeaker, alias_row.recording_speaker_id
                )
            if alias_speaker is None or alias_speaker.recording_id != recording_id:
                continue
            resolved = _resolve_active_recording_speaker(session, alias_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    for recording_speaker in recording_speakers:
        if recording_speaker.diarization_label == value:
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    for recording_speaker in recording_speakers:
        if matches_speaker_name(recording_speaker.local_name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved
        if matches_speaker_name(recording_speaker.name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved
        global_speaker = getattr(recording_speaker, "global_speaker", None)
        if global_speaker and matches_speaker_name(global_speaker.name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    if not speaker_ids:
        return None

    alias_rows = (
        session.execute(
            select(RecordingSpeakerAlias)
            .where(RecordingSpeakerAlias.recording_speaker_id.in_(speaker_ids))
            .where(RecordingSpeakerAlias.active.is_(True))
            .where(RecordingSpeakerAlias.alias_value == value)
            .order_by(RecordingSpeakerAlias.id.desc())
        )
        .scalars()
        .all()
    )
    speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}
    for alias_row in alias_rows:
        alias_speaker = speakers_by_id.get(alias_row.recording_speaker_id)
        if alias_speaker is None:
            alias_speaker = session.get(
                RecordingSpeaker, alias_row.recording_speaker_id
            )
        if alias_speaker is None or alias_speaker.recording_id != recording_id:
            continue
        resolved = _resolve_active_recording_speaker(session, alias_speaker)
        _apply_source_run_provenance(session, resolved, source_run_id)
        return resolved

    return None
