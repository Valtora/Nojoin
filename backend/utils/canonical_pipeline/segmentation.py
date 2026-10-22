from .constants import *
from .speaker import *


def _active_speaker_labels(
    recording_speakers_by_id: dict[int, RecordingSpeaker],
) -> dict[str, str]:
    """Each diarisation label, mapped to the label of the speaker it now is.

    A merged row maps to the end of its merge chain. The stored projection keeps
    overlap labels as diarised, so an unmerge hands them back unchanged; reads
    pass them through this map so a merged label never reaches a reader.
    """
    active: dict[str, str] = {}
    for speaker in recording_speakers_by_id.values():
        if not speaker.diarization_label:
            continue
        current = speaker
        visited: set[int] = set()
        while current.merged_into_id and current.id not in visited:
            visited.add(current.id)
            target = recording_speakers_by_id.get(int(current.merged_into_id))
            if target is None:
                break
            current = target
        active[speaker.diarization_label] = (
            current.diarization_label or speaker.diarization_label
        )
    return active


def _rolling_overlap_labels_for_utterance(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
    recording_speakers_by_id: dict[int, RecordingSpeaker] | None = None,
    active_labels: dict[str, str] | None = None,
) -> list[str]:
    """The labels overlapping a line, as stored, or resolved by ``active_labels``."""
    projection = projection or {}
    labels = _stored_overlap_labels(
        utterance,
        projection=projection,
        recording_speakers_by_id=recording_speakers_by_id,
    )
    if active_labels is None:
        return labels
    return remap_overlapping_speakers(
        labels,
        primary=utterance.speaker_label or projection.get("speaker") or UNKNOWN_SPEAKER,
        mapping=active_labels,
    )


def _stored_overlap_labels(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any],
    recording_speakers_by_id: dict[int, RecordingSpeaker] | None,
) -> list[str]:
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


def _projection_overlap_labels(
    index: int,
    segments: Sequence[dict[str, Any]],
    overlap_groups: dict[int, dict[str, Any]],
    recording_speakers: Iterable[RecordingSpeaker],
) -> list[str]:
    source_segment = segments[index]
    existing = list(source_segment.get("overlapping_speakers") or [])
    if existing:
        return existing

    group = overlap_groups.get(index)
    if not group:
        return []
    labels: list[str] = []
    for other_index in group["members"]:
        if other_index == index:
            continue
        label = str(segments[other_index].get("speaker") or UNKNOWN_SPEAKER)
        if label not in labels and label != source_segment.get("speaker"):
            labels.append(label)
    return labels


def _speaker_state_for_utterance(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    if utterance.manual_speaker_locked:
        return ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE

    confidence_payload = dict(utterance.confidence_payload or {})
    live_label = confidence_payload.get(LIVE_SPEAKER_LABEL_PAYLOAD_KEY)
    if (
        isinstance(live_label, dict)
        and utterance.state == TranscriptUtteranceState.PROVISIONAL
    ):
        if live_label.get("locked") is True:
            return ROLLING_DIARIZATION_SPEAKER_STATE_STABLE
        return ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL

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
