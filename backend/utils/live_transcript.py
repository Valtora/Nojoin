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


def _coerce_seconds_from_ms(value: Any) -> float | None:
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return None


def _extract_reusable_words(segment: dict[str, Any]) -> list[dict[str, Any]]:
    confidence_payload = segment.get("confidence_payload")
    if not isinstance(confidence_payload, dict):
        return []

    words: list[dict[str, Any]] = []
    for asr_segment in confidence_payload.get("asr_segments") or []:
        if not isinstance(asr_segment, dict):
            continue
        for word_payload in asr_segment.get("words") or []:
            if not isinstance(word_payload, dict):
                continue
            word_text = str(word_payload.get("word") or "").strip()
            start = _coerce_seconds_from_ms(word_payload.get("start_ms"))
            end = _coerce_seconds_from_ms(word_payload.get("end_ms"))
            if not word_text or start is None or end is None or end <= start:
                continue
            words.append({"start": start, "end": end, "word": word_text})

    words.sort(key=lambda word: (float(word["start"]), float(word["end"])))
    return words


def build_transcription_result_from_segments(
    segments: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    reusable_segments: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        reusable_segment = {
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "text": text,
        }
        words = _extract_reusable_words(segment)
        if words:
            reusable_segment["words"] = words
        reusable_segments.append(reusable_segment)

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

        if live_label and _is_clear_microphone_source_segment(live_segment):
            next_segment["speaker"] = live_label
            if not _source_channel_activity(live_segment).get("source_overlap"):
                next_segment["overlapping_speakers"] = []
            next_segment["speaker_state"] = "stable"
            next_segment["speaker_state_source"] = "source_channel"

        if live_segment.get("speaker_manually_edited") is True and live_label:
            next_segment["speaker"] = live_label
            next_segment["overlapping_speakers"] = []
            next_segment["speaker_manually_edited"] = True

        if live_segment.get("text_manually_edited") is True:
            next_segment["text_manually_edited"] = True

        authoritative_segments.append(next_segment)

    return authoritative_segments


def _source_channel_activity(segment: dict[str, Any]) -> dict[str, Any]:
    confidence_payload = segment.get("confidence_payload")
    if not isinstance(confidence_payload, dict):
        return {}
    source_activity = confidence_payload.get("source_channel_activity")
    if not isinstance(source_activity, dict):
        return {}
    return source_activity


def _is_clear_microphone_source_segment(segment: dict[str, Any]) -> bool:
    source_activity = _source_channel_activity(segment)
    if source_activity.get("dominant_source") != "microphone":
        return False
    try:
        confidence = float(segment.get("speaker_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence >= 0.65


def map_final_speakers_to_live_labels(
    live_segments: list[dict[str, Any]],
    combined_segments: list[dict[str, Any]],
) -> dict[str, str]:
    scores: dict[str, dict[str, float]] = {}

    for combined_segment in combined_segments:
        final_label = combined_segment.get("speaker")
        if not final_label:
            continue

        for live_segment in live_segments:
            if live_segment.get("speaker_manually_edited") is True:
                continue
            live_label = live_segment.get("speaker")
            if not live_label or str(live_label).strip().upper() == "UNKNOWN":
                continue
            overlap = _segment_overlap_seconds(combined_segment, live_segment)
            if overlap <= 0.0:
                continue

            label_scores = scores.setdefault(str(final_label), {})
            label_scores[str(live_label)] = label_scores.get(str(live_label), 0.0) + overlap

    mapping: dict[str, str] = {}
    for final_label, label_scores in scores.items():
        best_live_label = max(label_scores.items(), key=lambda item: item[1])[0]
        mapping[final_label] = best_live_label

    return mapping


def _segment_overlap_seconds(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float:
    try:
        start = max(float(first.get("start", 0.0)), float(second.get("start", 0.0)))
        end = min(float(first.get("end", 0.0)), float(second.get("end", 0.0)))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, end - start)


def _segment_merge_key(segment: dict[str, Any]) -> tuple[float, float, str]:
    return (
        round(float(segment.get("start", 0.0)), 3),
        round(float(segment.get("end", 0.0)), 3),
        str(segment.get("text", "")).strip(),
    )