from __future__ import annotations

from typing import Any


LIVE_SPEAKER_PREFIX = "LIVE_"


def is_live_label(label: str | None) -> bool:
    return bool(label) and str(label).startswith(LIVE_SPEAKER_PREFIX)


def is_live_segment(segment: dict[str, Any]) -> bool:
    return bool(
        segment.get("segment_source") == "live"
        or segment.get("provisional") is True
        or is_live_label(segment.get("speaker"))
    )


def build_transcription_result_from_segments(
    segments: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    reusable_segments = [
        {
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "text": str(segment.get("text", "")).strip(),
        }
        for segment in segments
        if str(segment.get("text", "")).strip()
    ]

    if not reusable_segments:
        return None, []

    return (
        {
            "text": " ".join(segment["text"] for segment in reusable_segments),
            "segments": reusable_segments,
        },
        reusable_segments,
    )


def merge_reusable_segments(
    primary_segments: list[dict[str, Any]],
    additional_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged_segments: list[dict[str, Any]] = [dict(segment) for segment in primary_segments]
    seen_keys = {_segment_merge_key(segment) for segment in merged_segments}

    for segment in additional_segments:
        segment_key = _segment_merge_key(segment)
        if segment_key in seen_keys:
            continue
        merged_segments.append(dict(segment))
        seen_keys.add(segment_key)

    merged_segments.sort(
        key=lambda segment: (
            float(segment.get("start", 0.0)),
            float(segment.get("end", 0.0)),
            str(segment.get("text", "")),
        )
    )
    return merged_segments


def apply_live_authority_to_segments(
    live_segments: list[dict[str, Any]],
    combined_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not live_segments or not combined_segments:
        return combined_segments

    authoritative_segments: list[dict[str, Any]] = []
    for index, combined_segment in enumerate(combined_segments):
        next_segment = dict(combined_segment)
        if index >= len(live_segments):
            authoritative_segments.append(next_segment)
            continue

        live_segment = live_segments[index]
        live_text = str(live_segment.get("text", "")).strip()
        if live_text:
            next_segment["text"] = live_text

        live_label = live_segment.get("speaker")
        if live_label:
            next_segment["live_source_speaker"] = live_label

        if live_segment.get("speaker_manually_edited") is True and live_label:
            next_segment["speaker"] = live_label
            next_segment["overlapping_speakers"] = []
            next_segment["speaker_manually_edited"] = True

        if live_segment.get("text_manually_edited") is True:
            next_segment["text_manually_edited"] = True

        authoritative_segments.append(next_segment)

    return authoritative_segments


def map_final_speakers_to_live_labels(
    live_segments: list[dict[str, Any]],
    combined_segments: list[dict[str, Any]],
) -> dict[str, str]:
    scores: dict[str, dict[str, float]] = {}

    for index, combined_segment in enumerate(combined_segments):
        if index >= len(live_segments):
            break

        live_segment = live_segments[index]
        if live_segment.get("speaker_manually_edited") is True:
            continue

        final_label = combined_segment.get("speaker")
        live_label = live_segment.get("speaker") or combined_segment.get(
            "live_source_speaker"
        )
        if not final_label or not live_label:
            continue

        duration = max(
            0.0,
            float(combined_segment.get("end", 0.0))
            - float(combined_segment.get("start", 0.0)),
        )
        if duration <= 0.0:
            duration = 1.0

        label_scores = scores.setdefault(str(final_label), {})
        label_scores[str(live_label)] = label_scores.get(str(live_label), 0.0) + duration

    mapping: dict[str, str] = {}
    for final_label, label_scores in scores.items():
        best_live_label = max(label_scores.items(), key=lambda item: item[1])[0]
        mapping[final_label] = best_live_label

    return mapping


def _segment_merge_key(segment: dict[str, Any]) -> tuple[float, float, str]:
    return (
        round(float(segment.get("start", 0.0)), 3),
        round(float(segment.get("end", 0.0)), 3),
        str(segment.get("text", "")).strip(),
    )