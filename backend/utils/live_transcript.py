from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


LIVE_SPEAKER_PREFIX = "LIVE_"
LIVE_FINAL_REUSE_MIN_OVERLAP_RATIO = 0.60
LIVE_FINAL_REUSE_MIN_DURATION_RATIO = 0.60
LIVE_FINAL_REUSE_AMBIGUITY_MARGIN = 0.12
LIVE_FINAL_SPEAKER_MAP_MIN_SHARE = 0.60
LIVE_FINAL_SPEAKER_MAP_MIN_MARGIN = 0.15


@dataclass(frozen=True)
class _LiveFinalMatchCandidate:
    live_index: int
    reason: str
    overlap_seconds: float
    combined_overlap_ratio: float
    live_overlap_ratio: float
    duration_ratio: float
    text_similarity: float

    @property
    def score(self) -> float:
        return (
            self.combined_overlap_ratio
            + self.live_overlap_ratio
            + self.duration_ratio
            + (self.text_similarity * 0.25)
        )


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

    public_id = _segment_public_id(segment)
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
            reusable_word = {"start": start, "end": end, "word": word_text}
            if public_id:
                reusable_word["source_public_id"] = public_id
            words.append(reusable_word)

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
        public_id = _segment_public_id(segment)
        if public_id:
            reusable_segment["id"] = public_id
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

    matches_by_combined_index, rejections_by_combined_index = _match_live_segments_to_combined(
        live_segments,
        combined_segments,
    )
    authoritative_segments: list[dict[str, Any]] = []
    for combined_index, combined_segment in enumerate(combined_segments):
        next_segment = dict(combined_segment)
        match_candidate = matches_by_combined_index.get(combined_index)
        if match_candidate is None:
            next_segment["live_reuse_alignment"] = _build_rejected_alignment_payload(
                rejections_by_combined_index.get(combined_index),
                live_segments,
            )
            authoritative_segments.append(next_segment)
            continue

        live_segment = live_segments[match_candidate.live_index]
        manual_override_reasons: list[str] = []
        live_text = str(live_segment.get("text", "")).strip()
        if live_text:
            next_segment["text"] = live_text

        live_label = live_segment.get("speaker")
        if live_label:
            next_segment["live_source_speaker"] = live_label

        if live_label and _is_unresolved_speaker_label(next_segment.get("speaker")):
            next_segment["speaker"] = live_label
            next_segment["speaker_state_source"] = "live_reuse"

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
            manual_override_reasons.append("manual_speaker_locked")

        if live_segment.get("text_manually_edited") is True:
            next_segment["text_manually_edited"] = True
            manual_override_reasons.append("manual_text_locked")

        next_segment["live_reuse_alignment"] = _build_matched_alignment_payload(
            match_candidate,
            live_segment,
            manual_override_reasons,
        )

        authoritative_segments.append(next_segment)

    return authoritative_segments


def _match_live_segments_to_combined(
    live_segments: list[dict[str, Any]],
    combined_segments: list[dict[str, Any]],
) -> tuple[dict[int, _LiveFinalMatchCandidate], dict[int, _LiveFinalMatchCandidate | str]]:
    matches_by_combined_index: dict[int, _LiveFinalMatchCandidate] = {}
    rejections_by_combined_index: dict[int, _LiveFinalMatchCandidate | str] = {}
    live_ids: dict[str, int] = {}
    duplicate_live_ids: set[str] = set()

    for live_index, live_segment in enumerate(live_segments):
        public_id = _segment_public_id(live_segment)
        if not public_id:
            continue
        if public_id in live_ids:
            duplicate_live_ids.add(public_id)
            continue
        live_ids[public_id] = live_index

    stable_id_candidates: dict[int, _LiveFinalMatchCandidate] = {}
    stable_live_usage: dict[int, list[int]] = {}
    for combined_index, combined_segment in enumerate(combined_segments):
        public_id = _segment_public_id(combined_segment)
        if not public_id:
            continue
        if public_id in duplicate_live_ids:
            rejections_by_combined_index[combined_index] = "duplicate_live_utterance_id"
            continue
        live_index = live_ids.get(public_id)
        if live_index is None:
            continue
        candidate = _build_match_candidate(
            live_segments[live_index],
            combined_segment,
            live_index=live_index,
            reason="stable_utterance_id",
        )
        stable_id_candidates[combined_index] = candidate
        stable_live_usage.setdefault(live_index, []).append(combined_index)

    for live_index, combined_indexes in stable_live_usage.items():
        if len(combined_indexes) > 1:
            for combined_index in combined_indexes:
                rejections_by_combined_index[combined_index] = "ambiguous_stable_utterance_id"
            continue
        combined_index = combined_indexes[0]
        matches_by_combined_index[combined_index] = stable_id_candidates[combined_index]

    used_live_indexes = {
        candidate.live_index for candidate in matches_by_combined_index.values()
    }
    tentative_matches: dict[int, _LiveFinalMatchCandidate] = {}
    for combined_index, combined_segment in enumerate(combined_segments):
        if combined_index in matches_by_combined_index or combined_index in rejections_by_combined_index:
            continue

        all_candidates = [
            _build_match_candidate(
                live_segment,
                combined_segment,
                live_index=live_index,
                reason="time_overlap",
            )
            for live_index, live_segment in enumerate(live_segments)
            if live_index not in used_live_indexes
        ]
        usable_candidates = [candidate for candidate in all_candidates if _candidate_is_usable(candidate)]
        usable_candidates.sort(key=lambda candidate: candidate.score, reverse=True)

        if not usable_candidates:
            best_candidate = max(all_candidates, key=lambda candidate: candidate.score, default=None)
            rejections_by_combined_index[combined_index] = best_candidate or "no_live_overlap"
            continue

        best_candidate = usable_candidates[0]
        if len(usable_candidates) > 1:
            second_candidate = usable_candidates[1]
            if (best_candidate.score - second_candidate.score) < LIVE_FINAL_REUSE_AMBIGUITY_MARGIN:
                rejections_by_combined_index[combined_index] = best_candidate
                continue

        tentative_matches[combined_index] = best_candidate

    live_usage: dict[int, list[int]] = {}
    for combined_index, candidate in tentative_matches.items():
        live_usage.setdefault(candidate.live_index, []).append(combined_index)

    for combined_index, candidate in tentative_matches.items():
        if len(live_usage.get(candidate.live_index, [])) > 1:
            rejections_by_combined_index[combined_index] = "ambiguous_live_utterance_reused"
            continue
        matches_by_combined_index[combined_index] = candidate

    return matches_by_combined_index, rejections_by_combined_index


def _candidate_is_usable(candidate: _LiveFinalMatchCandidate) -> bool:
    if candidate.overlap_seconds <= 0.0:
        return False
    if candidate.combined_overlap_ratio < LIVE_FINAL_REUSE_MIN_OVERLAP_RATIO:
        return False
    if candidate.live_overlap_ratio < LIVE_FINAL_REUSE_MIN_OVERLAP_RATIO:
        return False
    return candidate.duration_ratio >= LIVE_FINAL_REUSE_MIN_DURATION_RATIO


def _build_match_candidate(
    live_segment: dict[str, Any],
    combined_segment: dict[str, Any],
    *,
    live_index: int,
    reason: str,
) -> _LiveFinalMatchCandidate:
    overlap_seconds = _segment_overlap_seconds(combined_segment, live_segment)
    combined_duration = _segment_duration_seconds(combined_segment)
    live_duration = _segment_duration_seconds(live_segment)
    larger_duration = max(combined_duration, live_duration)
    smaller_duration = min(combined_duration, live_duration)
    return _LiveFinalMatchCandidate(
        live_index=live_index,
        reason=reason,
        overlap_seconds=overlap_seconds,
        combined_overlap_ratio=(overlap_seconds / combined_duration) if combined_duration > 0.0 else 0.0,
        live_overlap_ratio=(overlap_seconds / live_duration) if live_duration > 0.0 else 0.0,
        duration_ratio=(smaller_duration / larger_duration) if larger_duration > 0.0 else 0.0,
        text_similarity=_text_similarity(combined_segment.get("text"), live_segment.get("text")),
    )


def _build_matched_alignment_payload(
    candidate: _LiveFinalMatchCandidate,
    live_segment: dict[str, Any],
    manual_override_reasons: list[str],
) -> dict[str, Any]:
    payload = _candidate_alignment_payload(candidate, live_segment)
    payload["status"] = "matched"
    payload["reason"] = candidate.reason
    if manual_override_reasons:
        payload["manual_override_reasons"] = manual_override_reasons
    return payload


def _build_rejected_alignment_payload(
    rejection: _LiveFinalMatchCandidate | str | None,
    live_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(rejection, _LiveFinalMatchCandidate):
        payload = _candidate_alignment_payload(rejection, live_segments[rejection.live_index])
        payload["candidate_live_utterance_ids"] = payload.pop("matched_live_utterance_ids", [])
        payload["candidate_live_speaker"] = payload.pop("matched_live_speaker", None)
        payload["matched_live_utterance_ids"] = []
        payload["status"] = "rejected"
        payload["reason"] = "insufficient_or_ambiguous_overlap"
        return payload
    return {
        "status": "rejected",
        "reason": rejection or "no_live_match",
        "matched_live_utterance_ids": [],
    }


def _candidate_alignment_payload(
    candidate: _LiveFinalMatchCandidate,
    live_segment: dict[str, Any],
) -> dict[str, Any]:
    public_id = _segment_public_id(live_segment)
    return {
        "matched_live_utterance_ids": [public_id] if public_id else [],
        "matched_live_speaker": live_segment.get("speaker"),
        "overlap_seconds": round(candidate.overlap_seconds, 3),
        "combined_overlap_ratio": round(candidate.combined_overlap_ratio, 3),
        "live_overlap_ratio": round(candidate.live_overlap_ratio, 3),
        "duration_ratio": round(candidate.duration_ratio, 3),
        "text_similarity": round(candidate.text_similarity, 3),
    }


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
        ranked_labels = sorted(label_scores.items(), key=lambda item: item[1], reverse=True)
        best_live_label, best_score = ranked_labels[0]
        total_score = sum(label_scores.values())
        best_share = (best_score / total_score) if total_score > 0.0 else 0.0
        second_score = ranked_labels[1][1] if len(ranked_labels) > 1 else 0.0
        margin = ((best_score - second_score) / total_score) if total_score > 0.0 else 0.0
        if best_share < LIVE_FINAL_SPEAKER_MAP_MIN_SHARE:
            continue
        if len(ranked_labels) > 1 and margin < LIVE_FINAL_SPEAKER_MAP_MIN_MARGIN:
            continue
        mapping[final_label] = best_live_label

    return mapping


def _segment_public_id(segment: dict[str, Any]) -> str | None:
    for key in ("id", "public_id", "live_utterance_id", "source_public_id"):
        value = segment.get(key)
        if value is None:
            continue
        public_id = str(value).strip()
        if public_id:
            return public_id
    return None


def _segment_duration_seconds(segment: dict[str, Any]) -> float:
    try:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, end - start)


def _is_unresolved_speaker_label(label: Any) -> bool:
    return str(label or "").strip().upper() in {"", "UNKNOWN"}


def _text_similarity(first: Any, second: Any) -> float:
    first_text = " ".join(str(first or "").lower().split())
    second_text = " ".join(str(second or "").lower().split())
    if not first_text or not second_text:
        return 0.0
    if first_text == second_text:
        return 1.0
    return float(SequenceMatcher(None, first_text, second_text).ratio())


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