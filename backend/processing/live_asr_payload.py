"""Context trimming, repetition filtering, and payloads for live ASR results."""

import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.processing.live_audio_buffer import (
    LiveSourceChannelEvidence,
    _coerce_live_source_channel_evidence,
)
from backend.utils.word_times import exceeds


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
            words = seg.get("words") or []
            kept_words = []
            for word in words:
                word_text = str(word.get("word") or "").strip()
                if not word_text:
                    continue
                word_start = float(word.get("start", 0.0))
                word_end = float(word.get("end", word_start))
                kept_words.append(
                    {
                        "start": max(0.0, word_start - prefix_s),
                        "end": max(0.0, word_end - prefix_s),
                        "word": word_text,
                    }
                )
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
                word_start = float(word.get("start", 0.0))
                word_end = float(word.get("end", word_start))
                if not exceeds(word_end, prefix_s + EPS):
                    continue
                kept_words.append(
                    {
                        "start": max(0.0, word_start - prefix_s),
                        "end": max(0.0, word_end - prefix_s),
                        "word": word_text,
                    }
                )
            joined = " ".join(word["word"] for word in kept_words)
            if joined:
                kept.append(
                    {
                        "start": kept_words[0]["start"],
                        "end": kept_words[-1]["end"],
                        "text": joined,
                        "words": kept_words,
                    }
                )
        elif (start + end) / 2 >= prefix_s:
            kept.append(
                {
                    "start": 0.0,
                    "end": max(0.0, end - prefix_s),
                    "text": seg_text,
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
        words = []
        for word in segment.get("words") or []:
            word_text = str(word.get("word") or "").strip()
            if not word_text:
                continue
            words.append(
                {
                    "start_ms": int(
                        region_start_ms + round(float(word.get("start", 0.0)) * 1000.0)
                    ),
                    "end_ms": int(
                        region_start_ms + round(float(word.get("end", 0.0)) * 1000.0)
                    ),
                    "word": word_text,
                }
            )
        if words:
            has_word_timestamps = True
            absolute_segment["words"] = words
        absolute_segments.append(absolute_segment)

    payload: dict[str, Any] = {
        "utterance_start_ms": int(region_start_ms),
        "utterance_end_ms": int(region_end_ms),
        "asr_segments": absolute_segments,
        "asr_word_timestamps_available": has_word_timestamps,
    }
    if source_activity:
        payload["source_channel_activity"] = source_activity
    evidence = _coerce_live_source_channel_evidence(source_channel_evidence)
    if evidence:
        payload["source_channel_evidence"] = evidence.to_payload()
    return payload


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
