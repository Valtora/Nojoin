"""Construction and updates of canonical transcript projection segments."""

from .constants import *
from .segmentation import *


def _build_projection_segment(
    utterance: TranscriptUtterance,
    *,
    source_segment: dict[str, Any],
    recording_speaker: RecordingSpeaker | None,
    overlap_labels: list[str],
) -> dict[str, Any]:
    segment = dict(source_segment)
    segment.update(
        {
            "id": utterance.public_id,
            "start": utterance.start_ms / 1000.0,
            "end": utterance.end_ms / 1000.0,
            "text": utterance.text,
            "speaker": recording_speaker.diarization_label
            if recording_speaker
            else utterance.speaker_label or UNKNOWN_SPEAKER,
            "overlapping_speakers": overlap_labels,
            "provisional": utterance.state == TranscriptUtteranceState.PROVISIONAL,
            "segment_source": source_segment.get("segment_source")
            or utterance.source_kind,
            "speaker_manually_edited": utterance.manual_speaker_locked,
            "text_manually_edited": utterance.manual_text_locked,
            "text_edit_source": utterance.text_last_edit_source,
            "speaker_edit_source": utterance.speaker_last_edit_source,
            "speaker_state": _speaker_state_for_utterance(
                utterance, projection=source_segment
            ),
            "revision": utterance.revision,
            "recording_speaker_id": utterance.recording_speaker_id,
            "diarization_label": utterance.diarization_label,
            "state": utterance.state.value
            if hasattr(utterance.state, "value")
            else str(utterance.state),
            "speaker_confidence": utterance.speaker_confidence,
            "text_confidence": utterance.text_confidence,
            "speaker_assignment_source": _resolve_segment_speaker_assignment_source(
                segment,
                source=str(
                    source_segment.get("segment_source") or utterance.source_kind
                ),
                state=utterance.state,
                manual_speaker_locked=bool(utterance.manual_speaker_locked),
            ),
            "speaker_assignment_authority": _resolve_segment_speaker_assignment_authority(
                segment,
                state=utterance.state,
                manual_speaker_locked=bool(utterance.manual_speaker_locked),
            ),
            "updated_at": utterance.updated_at.isoformat(),
        }
    )
    return segment


def _update_projection_segment_by_public_id(
    transcript: Transcript,
    utterance_public_id: str,
    updates: dict[str, Any],
) -> None:
    updated_segments = [dict(segment) for segment in (transcript.segments or [])]
    for index, segment in enumerate(updated_segments):
        if str(segment.get("id")) != utterance_public_id:
            continue
        updated_segments[index].update(updates)
        transcript.segments = updated_segments
        flag_modified(transcript, "segments")
        return


__all__ = [
    "_build_projection_segment",
    "_update_projection_segment_by_public_id",
]
