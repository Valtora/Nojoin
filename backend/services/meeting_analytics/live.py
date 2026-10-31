"""Read-only transcript estimates; audio and final analysis stay separate."""

from collections.abc import Sequence
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing.delivery_descriptors import (
    MIN_UTTERANCE_MS,
    MIN_UTTERANCES_PER_SPEAKER,
)
from backend.utils.canonical_pipeline.core import build_transcript_segments_for_read
from backend.utils.speaker_names.live import live_speaker_groups, live_speaker_names

from .metrics import (
    UtteranceRow,
    build_turns,
    compute_talk_time,
    compute_turn_structure,
    merge_intervals,
)
from .query import _build_speaker_directory, _describe_unlisted_key


def _speaker_key(segment: dict[str, Any]) -> str:
    speaker_id = segment.get("recording_speaker_id")
    if speaker_id is not None:
        return f"rs:{speaker_id}"
    return f"label:{segment.get('speaker') or 'UNKNOWN'}"


def _time_ms(segment: dict[str, Any], field: str) -> int:
    value = segment.get(f"{field}_ms")
    return (
        int(value)
        if value is not None
        else round(float(segment.get(field) or 0) * 1000)
    )


def _overlapped_indices(segments: Sequence[dict[str, Any]]) -> set[int]:
    """Find intersecting intervals as well as explicitly marked overlap."""
    overlapping = {
        index
        for index, segment in enumerate(segments)
        if segment.get("overlapping_speakers") or segment.get("overlap_group_id")
    }
    intervals = sorted(
        (_time_ms(segment, "start"), _time_ms(segment, "end"), index)
        for index, segment in enumerate(segments)
        if _time_ms(segment, "end") > _time_ms(segment, "start")
    )
    furthest_end, furthest_index = 0, -1
    for start, end, index in intervals:
        if furthest_index >= 0 and start < furthest_end:
            overlapping.update((index, furthest_index))
        if furthest_index < 0 or end > furthest_end:
            furthest_end, furthest_index = end, index
    return overlapping


def transcript_wpm(segments: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Existing per-utterance delivery pace, without reading acoustic data."""
    overlapping = _overlapped_indices(segments)
    samples: dict[str, list[float]] = {}
    results: dict[str, dict[str, Any]] = {}
    for index, segment in enumerate(segments):
        key = _speaker_key(segment)
        result = results.setdefault(
            key,
            {
                "words_per_minute": None,
                "sample_count": 0,
                "excluded_short": 0,
                "excluded_overlapping": 0,
            },
        )
        if index in overlapping:
            result["excluded_overlapping"] += 1
            continue
        duration = _time_ms(segment, "end") - _time_ms(segment, "start")
        if duration < MIN_UTTERANCE_MS:
            result["excluded_short"] += 1
            continue
        word_count = len(str(segment.get("text") or "").split())
        if word_count:
            samples.setdefault(key, []).append(
                round(word_count / (duration / 60_000), 1)
            )
    for key, result in results.items():
        readings = samples.get(key, [])
        result["sample_count"] = len(readings)
        if len(readings) >= MIN_UTTERANCES_PER_SPEAKER:
            result["words_per_minute"] = int(round(median(readings)))
    return results


def _analysed_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible = [
        segment
        for segment in segments
        if (segment.get("speaker") or "UNKNOWN") != "UNKNOWN"
        or segment.get("state") == "provisional"
        or segment.get("provisional") is True
    ]
    return visible or segments


def _continue_pending_speakers(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """An undiarised tail counts under the speaker before it, as the transcript shows.

    As there, only within one audio source.
    """
    continued: list[dict[str, Any]] = []
    for segment in segments:
        if (
            continued
            and segment.get("diarisation_pending") is True
            and (segment.get("speaker") or "UNKNOWN") == "UNKNOWN"
            and segment.get("source_channel") == continued[-1].get("source_channel")
        ):
            previous = continued[-1]
            segment = {
                **segment,
                "speaker": previous.get("speaker"),
                "recording_speaker_id": previous.get("recording_speaker_id"),
            }
        continued.append(segment)
    return continued


def _merge_live_speakers(
    segments: list[dict[str, Any]], groups: dict[str, str]
) -> list[dict[str, Any]]:
    """A live label the user merged counts as the label it joined, as the transcript shows it."""
    if not groups:
        return segments
    merged = []
    for segment in segments:
        speaker = groups.get(str(segment.get("speaker")), segment.get("speaker"))
        overlaps = segment.get("overlapping_speakers") or []
        if speaker != segment.get("speaker") or any(str(o) in groups for o in overlaps):
            segment = {
                **segment,
                "speaker": speaker,
                "overlapping_speakers": [
                    label
                    for label in dict.fromkeys(groups.get(str(o), o) for o in overlaps)
                    if label != speaker
                ],
            }
        merged.append(segment)
    return merged


def _resolve_projection_speakers(
    segments: list[dict[str, Any]], directory: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Legacy projections may carry a current label without its speaker id."""
    ids_by_label = {
        speaker["diarization_label"]: int(key.removeprefix("rs:"))
        for key, speaker in directory.items()
        if speaker["diarization_label"]
    }
    resolved: list[dict[str, Any]] = []
    for segment in segments:
        speaker_id = ids_by_label.get(segment.get("speaker"))
        if segment.get("recording_speaker_id") is None and speaker_id is not None:
            segment = {**segment, "recording_speaker_id": speaker_id}
        resolved.append(segment)
    return resolved


def _live_talk_time(
    rows: Sequence[UtteranceRow], duration_ms: int
) -> dict[str, dict[str, float]]:
    """Count a resolved speaker's speech once, retaining overlap with others."""
    by_speaker: dict[str, list[UtteranceRow]] = {}
    for row in rows:
        by_speaker.setdefault(row.speaker_key, []).append(row)
    united = [
        UtteranceRow(key, start, end)
        for key, speaker_rows in by_speaker.items()
        for start, end in (merge_intervals(speaker_rows) or [(0, 0)])
    ]
    return compute_talk_time(united, duration_ms)


def compute_live_recording_analytics(
    session: Session, recording: Recording, transcript: Transcript | None
) -> dict[str, Any]:
    assert recording.id is not None
    directory = _build_speaker_directory(session, recording.id, active_only=True)
    segments = (
        _analysed_segments(
            build_transcript_segments_for_read(
                session, recording.id, transcript=transcript
            )
        )
        if transcript is not None
        else []
    )
    segments = _continue_pending_speakers(
        _merge_live_speakers(
            _resolve_projection_speakers(segments, directory),
            live_speaker_groups(session, recording.id),
        )
    )
    rows = [
        UtteranceRow(
            _speaker_key(segment), _time_ms(segment, "start"), _time_ms(segment, "end")
        )
        for segment in segments
    ]
    duration_ms = max((row.end_ms for row in rows), default=0)
    talk_time = _live_talk_time(rows, duration_ms)
    names = live_speaker_names(
        session,
        recording.id,
        labels={
            str(label)
            for segment in segments
            for label in [
                segment.get("speaker"),
                *(segment.get("overlapping_speakers") or []),
            ]
            if label
        },
    )
    speakers = [
        directory.get(key) or _describe_unlisted_key(key, names)
        for key in sorted(
            talk_time, key=lambda key: talk_time[key]["speech_ms"], reverse=True
        )
    ]
    return {
        "recording_id": recording.public_id,
        "provisional": True,
        "change_sequence": transcript.change_sequence if transcript is not None else 0,
        "speakers": speakers,
        "metrics": {
            "utterance_count": len(rows),
            "talk_time": talk_time,
            "turn_structure": compute_turn_structure(build_turns(rows)),
        },
        "transcript_wpm": transcript_wpm(segments),
    }
