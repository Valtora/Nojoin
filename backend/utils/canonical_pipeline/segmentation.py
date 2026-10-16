from .constants import *
from .speaker import *


def _rolling_overlap_labels_for_utterance(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
    recording_speakers_by_id: dict[int, RecordingSpeaker] | None = None,
) -> list[str]:
    projection = projection or {}
    confidence_payload = dict(utterance.confidence_payload or {})
    rolling_payload = dict(confidence_payload.get("rolling_diarization") or {})

    if (
        "overlapping_recording_speaker_ids" in rolling_payload
        or "overlapping_speakers" in rolling_payload
    ):
        labels: list[str] = []
        for speaker_id in (
            rolling_payload.get("overlapping_recording_speaker_ids") or []
        ):
            try:
                speaker_id_value = int(speaker_id)
            except (TypeError, ValueError):
                continue
            speaker = (
                recording_speakers_by_id.get(speaker_id_value)
                if recording_speakers_by_id is not None
                else None
            )
            label = (
                str(speaker.diarization_label or "").strip()
                if speaker is not None
                else ""
            )
            if (
                label
                and label != (utterance.speaker_label or UNKNOWN_SPEAKER)
                and label not in labels
            ):
                labels.append(label)
        for label in rolling_payload.get("overlapping_speakers") or []:
            label_value = str(label or "").strip()
            if not label_value or label_value == (
                utterance.speaker_label or UNKNOWN_SPEAKER
            ):
                continue
            if label_value not in labels:
                labels.append(label_value)
        return labels

    return list(projection.get("overlapping_speakers") or [])


def _speaker_state_for_utterance(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    if utterance.manual_speaker_locked:
        return ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE

    confidence_payload = dict(utterance.confidence_payload or {})
    rolling_payload = dict(confidence_payload.get("rolling_diarization") or {})
    if rolling_payload.get("speaker_state"):
        return str(rolling_payload["speaker_state"])

    if projection and projection.get("speaker_state"):
        return str(projection["speaker_state"])

    state_value = (
        utterance.state.value
        if hasattr(utterance.state, "value")
        else str(utterance.state)
    )
    if state_value == TranscriptUtteranceState.FINALIZED.value:
        return ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
    if (
        utterance.source_kind in {"live", "catch_up"}
        or state_value == TranscriptUtteranceState.PROVISIONAL.value
    ):
        return ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL
    return ROLLING_DIARIZATION_SPEAKER_STATE_STABLE


__all__ = [name for name in globals() if not name.startswith("__")]
