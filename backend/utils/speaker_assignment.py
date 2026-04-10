from __future__ import annotations

from typing import Any


def normalise_speaker_name(name: str) -> str:
    return name.strip().casefold()


def matches_speaker_name(candidate: str | None, target: str) -> bool:
    return bool(candidate) and normalise_speaker_name(candidate) == normalise_speaker_name(target)


def segment_references_label(segment: dict[str, Any], label: str) -> bool:
    overlapping_labels = segment.get("overlapping_speakers") or []
    return segment.get("speaker") == label or label in overlapping_labels


def segments_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_start = float(first.get("start", 0.0))
    first_end = float(first.get("end", 0.0))
    second_start = float(second.get("start", 0.0))
    second_end = float(second.get("end", 0.0))
    return first_start < second_end and second_start < first_end


def _canonicalise_overlapping_speakers(
    segment: dict[str, Any],
    replace_from: str | None = None,
    replace_to: str | None = None,
) -> bool:
    overlapping_labels = segment.get("overlapping_speakers")
    if not isinstance(overlapping_labels, list):
        return False

    updated_labels: list[str] = []
    changed = False
    primary_label = segment.get("speaker")

    for label in overlapping_labels:
        next_label = replace_to if replace_from is not None and label == replace_from else label

        if next_label != label:
            changed = True

        if next_label == primary_label or next_label in updated_labels:
            changed = True
            continue

        updated_labels.append(next_label)

    if changed or updated_labels != overlapping_labels:
        segment["overlapping_speakers"] = updated_labels
        return True

    return False


def reconcile_segment_assignment(
    segments: list[dict[str, Any]],
    segment_index: int,
    old_label: str | None,
    new_label: str,
) -> bool:
    current_segment = segments[segment_index]
    changed = current_segment.get("speaker") != new_label
    current_segment["speaker"] = new_label

    replace_from = old_label if old_label and old_label != new_label else None
    if _canonicalise_overlapping_speakers(current_segment, replace_from, new_label):
        changed = True

    if replace_from is None:
        return changed

    for index, other_segment in enumerate(segments):
        if index == segment_index or not segments_overlap(current_segment, other_segment):
            continue

        if _canonicalise_overlapping_speakers(other_segment, replace_from, new_label):
            changed = True

    return changed