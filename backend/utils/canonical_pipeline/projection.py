"""Read-model projection for canonical utterances.

Split from core.py, which is size-capped, but logically one surface: turning
stored utterances (or, for a recording that predates the canonical pipeline,
the transcript's own JSON segments) into the payload the API returns. Nothing
here writes, and core.py calls none of it, so the dependency runs one way.
"""

from typing import Any

from backend.models.pipeline import TranscriptUtteranceState
from backend.models.transcript import Transcript

from .core import (
    ACTIVE_UTTERANCE_STATES,
    TOMBSTONE_UTTERANCE_STATES,
    UNKNOWN_SPEAKER,
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    _segment_to_ms,
    _to_optional_float,
    get_canonical_transcript_revision,
    select,
    serialize_canonical_utterances,
)
from .speaker import (
    _derive_default_speaker_assignment_authority,
    _derive_default_speaker_assignment_source,
    _normalize_speaker_assignment_authority,
    _normalize_speaker_assignment_source,
)


def serialize_canonical_delta(
    session,
    recording_id: int,
    *,
    after_revision: int | None = None,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    revision = get_canonical_transcript_revision(session, recording_id)
    if after_revision is None or after_revision <= 0:
        return revision, serialize_canonical_utterances(session, recording_id), []

    event_rows = session.execute(
        select(
            TranscriptUtteranceEvent.event_type,
            TranscriptUtterance.public_id,
            TranscriptUtterance.state,
        )
        .join(
            TranscriptUtterance,
            TranscriptUtterance.id == TranscriptUtteranceEvent.utterance_id,
        )
        .where(TranscriptUtteranceEvent.recording_id == recording_id)
        .where(TranscriptUtteranceEvent.id > after_revision)
        .order_by(TranscriptUtteranceEvent.id)
    ).all()

    changed_public_ids: set[str] = set()
    tombstones: list[str] = []
    tombstone_ids: set[str] = set()

    for event_type, public_id, state in event_rows:
        if not public_id:
            continue
        state_value = state.value if hasattr(state, "value") else str(state)
        if (
            event_type in {"supersede", "delete"}
            or state_value in TOMBSTONE_UTTERANCE_STATES
        ):
            changed_public_ids.discard(public_id)
            if public_id not in tombstone_ids:
                tombstone_ids.add(public_id)
                tombstones.append(public_id)
            continue
        if state_value in ACTIVE_UTTERANCE_STATES:
            changed_public_ids.add(public_id)

    return (
        revision,
        serialize_canonical_utterances(
            session,
            recording_id,
            only_public_ids=changed_public_ids,
        ),
        tombstones,
    )


def build_transient_utterance_payloads_from_segments(
    transcript: Transcript | None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, segment in enumerate((transcript.segments or []) if transcript else []):
        payloads.append(
            {
                "id": segment.get("id") or f"legacy-{index}",
                "start": float(segment.get("start", 0.0)),
                "end": float(segment.get("end", 0.0)),
                "start_ms": _segment_to_ms(segment.get("start", 0.0)),
                "end_ms": _segment_to_ms(segment.get("end", 0.0)),
                "text": str(segment.get("text", "") or ""),
                "speaker": str(segment.get("speaker") or UNKNOWN_SPEAKER),
                "recording_speaker_id": segment.get("recording_speaker_id"),
                "state": str(
                    segment.get("state")
                    or (
                        TranscriptUtteranceState.PROVISIONAL.value
                        if segment.get("provisional")
                        else TranscriptUtteranceState.STABLE.value
                    )
                ),
                "revision": int(segment.get("revision") or 1),
                "segment_source": segment.get("segment_source") or "legacy",
                "provisional": bool(segment.get("provisional") is True),
                "speaker_manually_edited": bool(
                    segment.get("speaker_manually_edited") is True
                ),
                "text_manually_edited": bool(
                    segment.get("text_manually_edited") is True
                ),
                # A pre-canonical recording cannot be edited over MCP: every
                # correction tool refuses one, so its edits are web edits and
                # carrying no source is the accurate answer rather than a gap.
                "text_edit_source": segment.get("text_edit_source"),
                "speaker_edit_source": segment.get("speaker_edit_source"),
                "speaker_state": segment.get("speaker_state"),
                "speaker_confidence": _to_optional_float(
                    segment.get("speaker_confidence")
                ),
                "text_confidence": _to_optional_float(segment.get("text_confidence")),
                "speaker_assignment_source": _normalize_speaker_assignment_source(
                    segment.get("speaker_assignment_source")
                    or _derive_default_speaker_assignment_source(
                        source=str(segment.get("segment_source") or "legacy"),
                        source_kind=str(segment.get("segment_source") or "legacy"),
                        state=str(
                            segment.get("state")
                            or (
                                TranscriptUtteranceState.PROVISIONAL.value
                                if segment.get("provisional")
                                else TranscriptUtteranceState.STABLE.value
                            )
                        ),
                        manual_speaker_locked=bool(
                            segment.get("speaker_manually_edited") is True
                        ),
                    )
                ),
                "speaker_assignment_authority": _normalize_speaker_assignment_authority(
                    segment.get("speaker_assignment_authority")
                    or _derive_default_speaker_assignment_authority(
                        state=str(
                            segment.get("state")
                            or (
                                TranscriptUtteranceState.PROVISIONAL.value
                                if segment.get("provisional")
                                else TranscriptUtteranceState.STABLE.value
                            )
                        ),
                        manual_speaker_locked=bool(
                            segment.get("speaker_manually_edited") is True
                        ),
                    )
                ),
                "updated_at": segment.get("updated_at"),
                "overlapping_speakers": list(segment.get("overlapping_speakers") or []),
            }
        )
    return payloads
