"""Context trimming, repetition filtering, and payloads for live ASR results."""

import hashlib
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.processing.live_audio_buffer import (
    LiveSourceChannelEvidence,
    _coerce_live_source_channel_evidence,
)
from backend.utils.word_text import reconstruct_text_from_words
from backend.utils.word_times import exceeds, has_untimed_words, has_word_timing


def _region_word(word: dict[str, Any], prefix_s: float) -> dict[str, Any]:
    if word.get("source_id") and not has_word_timing(word):
        return {**word, "start": None, "end": None}
    word_start = float(word.get("start", 0.0))
    word_end = float(word.get("end", word_start))
    text = str(word.get("word") or "")
    return {
        **(word if word.get("source_id") else {}),
        "start": max(0.0, word_start - prefix_s),
        "end": max(0.0, word_end - prefix_s),
        "word": text if word.get("source_id") else text.strip(),
    }


def _build_live_utterance_public_id(
    *,
    recording_id: int,
    span_start_ms: int,
    span_end_ms: int,
    speaker_label: str,
    text: str,
) -> str:
    payload = "|".join(
        [
            str(recording_id),
            str(int(span_start_ms)),
            str(int(span_end_ms)),
            str(speaker_label or "UNKNOWN"),
            str(text or ""),
        ]
    )
    return str(uuid5(NAMESPACE_URL, f"live-utterance:{payload}"))


def _extract_region_text(result: dict, prefix_s: float) -> str:
    """Select, from an engine result for a context-prefixed clip, the text that
    belongs to the speech region (the audio after `prefix_s` seconds).

    The clip handed to the engine is `left_context ++ region`; `prefix_s` is the
    length of the left-context run-up. Segment/word timestamps are clip-relative.
    """
    kept_segments = _extract_region_segment_payloads(result, prefix_s)
    return re.sub(
        r"\s+",
        " ",
        " ".join(
            str(segment.get("text", "") or "").strip() for segment in kept_segments
        ),
    ).strip()


def _extract_region_segment_payloads(
    result: dict, prefix_s: float
) -> list[dict[str, Any]]:
    """Return the ASR segment payloads that belong to the region after prefix_s.

    The returned segment and word timings are region-relative, not clip-relative.
    """
    EPS = 0.10
    segments = result.get("segments") or []
    if not segments:
        if prefix_s <= 0 and (result.get("text") or "").strip():
            return [
                {
                    "start": 0.0,
                    "end": 0.0,
                    "text": (result.get("text") or "").strip(),
                }
            ]
        return []

    kept: list[dict[str, Any]] = []
    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        seg_text = (seg.get("text") or "").strip()
        if not seg_text:
            continue
        if not exceeds(end, prefix_s + EPS):
            # Pure context: entirely within the run-up.
            continue
        if exceeds(start, prefix_s - EPS):
            # Entirely within the region.
            kept_segment = {
                "start": max(0.0, start - prefix_s),
                "end": max(0.0, end - prefix_s),
                "text": seg_text,
            }
            if seg.get("alignment_error"):
                kept_segment["alignment_error"] = seg["alignment_error"]
            words = seg.get("words") or []
            kept_words = []
            for word in words:
                word_text = str(word.get("word") or "").strip()
                if not word_text:
                    continue
                kept_words.append(_region_word(word, prefix_s))
            if kept_words:
                kept_segment["words"] = kept_words
            kept.append(kept_segment)
            continue
        # Straddles the prefix boundary.
        words = seg.get("words")
        if words:
            kept_words = []
            for word in words:
                word_text = str(word.get("word") or "").strip()
                if not word_text:
                    continue
                untimed = word.get("source_id") and not has_word_timing(word)
                word_start = float(word.get("start", 0.0)) if not untimed else 0.0
                word_end = float(word.get("end", word_start)) if not untimed else 0.0
                if not untimed and not exceeds(word_end, prefix_s + EPS):
                    continue
                kept_words.append(_region_word(word, prefix_s))
            joined = (
                reconstruct_text_from_words(kept_words)
                if any(word.get("source_id") for word in kept_words)
                else " ".join(word["word"] for word in kept_words)
            )
            if joined:
                kept.append(
                    {
                        "start": 0.0
                        if has_untimed_words(seg)
                        else kept_words[0]["start"],
                        "end": max(0.0, end - prefix_s)
                        if has_untimed_words(seg)
                        else kept_words[-1]["end"],
                        "text": joined,
                        "words": kept_words,
                    }
                )
        elif seg.get("alignment_error") or (start + end) / 2 >= prefix_s:
            kept.append(
                {
                    "start": 0.0,
                    "end": max(0.0, end - prefix_s),
                    "text": seg_text,
                    **(
                        {"alignment_error": seg["alignment_error"]}
                        if seg.get("alignment_error")
                        else {}
                    ),
                }
            )

    return kept


def _build_live_confidence_payload(
    *,
    region_segment_payloads: list[dict[str, Any]],
    region_start_ms: int,
    region_end_ms: int,
    source_activity: dict[str, Any] | None = None,
    source_channel_evidence: LiveSourceChannelEvidence | dict[str, Any] | None = None,
) -> dict[str, Any]:
    absolute_segments: list[dict[str, Any]] = []
    has_word_timestamps = False

    for segment in region_segment_payloads:
        absolute_segment = {
            "start_ms": int(
                region_start_ms + round(float(segment.get("start", 0.0)) * 1000.0)
            ),
            "end_ms": int(
                region_start_ms + round(float(segment.get("end", 0.0)) * 1000.0)
            ),
            "text": str(segment.get("text", "") or ""),
        }
        if segment.get("alignment_error"):
            absolute_segment["alignment_error"] = segment["alignment_error"]
        words = []
        for word in segment.get("words") or []:
            word_text = str(word.get("word") or "").strip()
            if not word_text:
                continue
            untimed = word.get("source_id") and not has_word_timing(word)
            words.append(
                {
                    **{
                        key: word[key]
                        for key in ("source_start", "source_end", "source_id")
                        if key in word
                    },
                    "start_ms": int(
                        region_start_ms + round(float(word.get("start", 0.0)) * 1000.0)
                    )
                    if not untimed
                    else None,
                    "end_ms": int(
                        region_start_ms + round(float(word.get("end", 0.0)) * 1000.0)
                    )
                    if not untimed
                    else None,
                    "word": str(word.get("word") or "")
                    if word.get("source_id")
                    else word_text,
                }
            )
        if words:
            has_word_timestamps = has_word_timestamps or any(
                word["start_ms"] is not None for word in words
            )
            absolute_segment["words"] = words
        absolute_segments.append(absolute_segment)

    payload: dict[str, Any] = {
        "utterance_start_ms": int(region_start_ms),
        "utterance_end_ms": int(region_end_ms),
        "asr_segments": absolute_segments,
        "asr_word_timestamps_available": has_word_timestamps,
    }
    errors = list(
        dict.fromkeys(
            segment["alignment_error"]
            for segment in absolute_segments
            if segment.get("alignment_error")
        )
    )
    if errors:
        payload["alignment_error"] = "; ".join(errors)
    if source_activity:
        payload["source_channel_activity"] = source_activity
    evidence = _coerce_live_source_channel_evidence(source_channel_evidence)
    if evidence:
        payload["source_channel_evidence"] = evidence.to_payload()
    return payload


def _native_text_spans(segment: dict[str, Any]) -> dict[str, Any]:
    """Map native tokens back to exact source spans without guessing wording."""
    words = segment.get("words") or []
    if not words or any(word.get("source_id") for word in words):
        return segment
    text = str(segment.get("text") or "")
    cursor = 0
    boundaries = [0]
    for index, word in enumerate(words):
        token = str(word.get("word") or "").strip()
        start = text.find(token, cursor) if token else -1
        if start < 0 or any(
            char.isalnum() or char in "'’" for char in text[cursor:start]
        ):
            return segment
        if index:
            gap = text[cursor:start]
            separator = next(
                (offset for offset, char in enumerate(gap) if char.isspace()), len(gap)
            )
            boundaries.append(cursor + separator)
        cursor = start + len(token)
    if any(char.isalnum() or char in "'’" for char in text[cursor:]):
        return segment
    boundaries.append(len(text))
    source_id = hashlib.sha256(
        f"native:{segment.get('start')}:{text}".encode()
    ).hexdigest()
    mapped = [
        {
            **word,
            "word": text[start:end],
            "source_start": start,
            "source_end": end,
            "source_id": source_id,
        }
        for word, start, end in zip(words, boundaries, boundaries[1:])
    ]
    return {**segment, "words": mapped}


def _prepare_live_region(
    result: dict, prefix_s: float, *, exact_spans: bool = False
) -> tuple[str, list[dict[str, Any]]]:
    """Filter text and word evidence together, retaining original text spans.

    The legacy lane (``exact_spans=False``) keeps upstream's textual filtering
    for native timings, whose tokens need not match the engine text, and filters
    forced wording and its evidence together.
    """
    mapped = result
    if not exact_spans:
        native = _extract_region_segment_payloads(result, prefix_s)
        forced = any(
            segment.get("alignment_error")
            or any(word.get("source_id") for word in segment.get("words") or [])
            for segment in native
        )
        if not forced:
            return _strip_repetition(_extract_region_text(result, prefix_s)), native
    if exact_spans:
        mapped = {
            **result,
            "segments": [
                _native_text_spans(segment) for segment in result.get("segments") or []
            ],
        }
    segments = _extract_region_segment_payloads(mapped, prefix_s)
    segments = _filter_region_repetitions(segments)
    if exact_spans:
        segments = [_native_text_spans(segment) for segment in segments]
    text = " ".join(str(segment.get("text") or "") for segment in segments).strip()
    if not exact_spans:
        text = re.sub(r"\s+", " ", text)
    return text, segments


def _filter_region_repetitions(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only repetitions that can be removed from text and word evidence."""
    if any(
        segment.get("alignment_error") or has_untimed_words(segment)
        for segment in segments
    ):
        # Unplaced words cannot identify which repetition belongs to new audio.
        return segments
    tokens = [
        token
        for segment in segments
        for token in str(segment.get("text") or "").split()
    ]
    target = _strip_repetition(" ".join(tokens)).split()
    if tokens == target:
        return segments
    selected: list[bool] = []
    cursor = 0
    for token in tokens:
        keep = cursor < len(target) and token == target[cursor]
        selected.append(keep)
        cursor += int(keep)
    return _select_region_tokens(segments, selected)


def _select_region_tokens(
    segments: list[dict[str, Any]], selected: list[bool]
) -> list[dict[str, Any]]:
    filtered = []
    cursor = 0
    for segment in segments:
        tokens = str(segment.get("text") or "").split()
        flags = selected[cursor : cursor + len(tokens)]
        cursor += len(tokens)
        words = segment.get("words") or []
        if not words:
            text = " ".join(token for token, keep in zip(tokens, flags) if keep)
            if text:
                filtered.append({**segment, "text": text})
            continue
        word_tokens = [str(word.get("word") or "").split() for word in words]
        if [token for group in word_tokens for token in group] != tokens:
            target = [token for token, keep in zip(tokens, flags) if keep]
            if [token for group in word_tokens for token in group] == target:
                filtered.append({**segment, "text": reconstruct_text_from_words(words)})
                continue
            return segments
        kept_words = _select_whole_words(words, word_tokens, flags)
        if kept_words is None:
            return segments
        if kept_words:
            filtered.append(
                {
                    **segment,
                    "words": kept_words,
                    "text": reconstruct_text_from_words(kept_words),
                }
            )
    return filtered


def _select_whole_words(
    words: list[dict[str, Any]], word_tokens: list[list[str]], flags: list[bool]
) -> list[dict[str, Any]] | None:
    kept = []
    cursor = 0
    for word, tokens in zip(words, word_tokens):
        selected = flags[cursor : cursor + len(tokens)]
        cursor += len(tokens)
        if any(selected) and not all(selected):
            return None
        if selected and all(selected):
            kept.append(word)
    return kept


def _strip_repetition(text: str) -> str:
    """Lightweight hallucination guard: collapse runs of repeated words or short
    phrases. Defensive only — on any doubt the text is returned unchanged.
    """
    if not text:
        return text
    words = text.split()
    if len(words) < 3:
        return text

    # Collapse a run of 3+ consecutive identical words to a single occurrence.
    deduped: list[str] = []
    i = 0
    n_words = len(words)
    while i < n_words:
        j = i
        while j < n_words and words[j] == words[i]:
            j += 1
        run = j - i
        deduped.extend([words[i]] if run >= 3 else words[i:j])
        i = j

    # Collapse a short phrase (2-5 words) repeated 3+ times consecutively.
    out: list[str] = []
    i = 0
    n = len(deduped)
    while i < n:
        collapsed = False
        for plen in range(2, 6):
            if i + plen * 3 > n:
                continue
            phrase = deduped[i : i + plen]
            reps = 1
            j = i + plen
            while j + plen <= n and deduped[j : j + plen] == phrase:
                reps += 1
                j += plen
            if reps >= 3:
                out.extend(phrase)
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(deduped[i])
            i += 1

    return " ".join(out)
