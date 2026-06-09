# backend/processing/live_transcribe.py
# Live transcription lane: a Celery task that transcribes recording segments
# as they arrive, producing provisional transcript segments. A sequence-gated
# buffer re-imposes ordering on concurrently uploaded segments and carries the
# trailing (incomplete) utterance forward across runs.

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_
from sqlmodel import select

from backend.celery_app import celery_app
from backend.models.pipeline import (
    DiarizationWindowResult,
    ProcessingRun,
    ProcessingRunKind,
    ProcessingRunStatus,
    RecordingAudioChunk,
    RecordingAudioWindowManifest,
    TranscriptUtteranceState,
)
from backend.processing.browser_live_audio import (
    BROWSER_LIVE_MICROPHONE_SOURCE,
    BROWSER_LIVE_SAMPLE_RATE_HZ,
    BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL,
    BROWSER_LIVE_SYSTEM_SOURCE,
)
from backend.processing.pipeline_metrics import (
    pipeline_metric_timer,
    record_pipeline_metric,
)
from backend.utils.asr_window_results import (
    build_recording_asr_window_result_config_hash,
    complete_recording_asr_window_result,
    fail_recording_asr_window_result,
    get_transcription_model_name,
    start_recording_asr_window_result,
)
from backend.utils.audio_windows import (
    WINDOW_DIARIZATION_STATUS_FAILED,
    WINDOW_DIARIZATION_STATUS_PROCESSED,
    WINDOW_DIARIZATION_STATUS_PROCESSING,
    WINDOW_STATUS_FAILED,
    WINDOW_STATUS_LIVE_PROCESSING,
    WINDOW_STATUS_LIVE_PROCESSED,
    infer_resume_state_from_manifests,
    mark_audio_windows_processed,
    window_asr_is_processed,
    window_diarization_status,
)
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
)
from backend.utils.config_manager import config_manager, is_meeting_edge_enabled
from backend.utils.recording_storage import recording_upload_temp_dir

logger = logging.getLogger(__name__)

# Tolerance (seconds) for treating a speech region as touching the buffer end.
TRAIL_EPS = 0.20
# Default maximum length (seconds) of a trailing utterance before a forced cut.
DEFAULT_FORCED_MAX = 8.0
# Absolute maximum length (seconds) of any emitted provisional live segment.
DEFAULT_MAX_SEGMENT_S = 20.0
# Sample rate of the canonical browser live-capture WAV.
LIVE_SAMPLE_RATE = BROWSER_LIVE_SAMPLE_RATE_HZ
# Silence threshold (ms) for the live lane. Set tight enough that natural
# Q&A handovers (e.g. a host's question followed immediately by a guest's
# answer with a < 500 ms pause) cleave into separate live utterances rather
# than arriving at reconciliation as one merged segment that absorbs both
# speakers' words. The previous 700 ms value commonly produced cross-speaker
# bleed at conversational transitions. Raise cautiously: lowering further
# may fragment a single speaker's normal pauses into many short utterances.
LIVE_MIN_SILENCE_MS = 320
LIVE_SPEAKER_MATCH_THRESHOLD = 0.72
LIVE_SPEAKER_MATCH_MARGIN = 0.05
LIVE_SPEAKER_SOFT_MATCH_THRESHOLD = 0.62
LIVE_SPEAKER_SOFT_MATCH_MARGIN = 0.12
LIVE_SYSTEM_SOURCE_SOFT_MATCH_THRESHOLD = 0.67
LIVE_SYSTEM_SOURCE_SOFT_MATCH_MARGIN = 0.16
LIVE_NEW_SPEAKER_THRESHOLD = 0.35
LIVE_GLOBAL_SPEAKER_MATCH_THRESHOLD = 0.78
LIVE_MIN_EMBEDDING_DURATION_S = 0.5
LIVE_VOICEPRINT_MIN_CLEAN_DURATION_S = 1.5
LIVE_RECORDING_SPEAKER_VOICEPRINT_ALPHA = 0.20
# Utterances at least this long extract their identification embedding from a
# centered sub-window rather than the full clip, so cross-speaker audio at
# the utterance boundaries does not contaminate recording-speaker voiceprints.
LIVE_EMBEDDING_CENTER_TRIM_MIN_DURATION_S = 2.0
LIVE_EMBEDDING_CENTER_TRIM_RATIO = 0.15
LIVE_GLOBAL_SPEAKER_VOICEPRINT_ALPHA = 0.10
LIVE_MIN_NEW_SPEAKER_DURATION_S = 2.0
LIVE_TAIL_RECONCILIATION_WINDOW_MS = 12_000
LIVE_INITIAL_SEQUENCE = 0
LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD = 0.65
LIVE_SOURCE_DOMINANCE_RATIO_THRESHOLD = 1.5
LIVE_SOURCE_OVERLAP_SHARE_THRESHOLD = 0.25
LIVE_SOURCE_AUTHORITY_CLEAR = "clear"
LIVE_SOURCE_AUTHORITY_OVERLAP = "overlap"
LIVE_SOURCE_AUTHORITY_NONE = "none"

_STATE_FILENAME = "state.json"
_BUFFER_FILENAME = "buffer.wav"
_CONTEXT_FILENAME = "context.wav"
_STATE_SOURCE_CHANNEL_LABELS_KEY = "source_channel_labels"
_STATE_SEQUENCE_OUTCOMES_KEY = "sequence_outcomes"
_MAX_LIVE_SEQUENCE_OUTCOMES = 200
_LIVE_SEQUENCE_OUTCOMES = {"consumed", "skipped", "deferred", "failed"}


@dataclass(frozen=True)
class LiveSourceChannelEvidence:
    dominant_source: str | None = None
    primary_source: str | None = None
    primary_share: float | None = None
    secondary_share: float | None = None
    source_overlap: bool = False
    authority: str = LIVE_SOURCE_AUTHORITY_NONE
    reason: str = "no_source_activity"
    preferred_label: str | None = None
    excluded_labels: tuple[str, ...] = ()
    speaker_confidence: float | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "authority": self.authority,
            "reason": self.reason,
            "dominant_source": self.dominant_source,
            "primary_source": self.primary_source,
            "source_overlap": self.source_overlap,
            "preferred_label": self.preferred_label,
            "excluded_labels": list(self.excluded_labels),
            "speaker_confidence": self.speaker_confidence,
        }
        if self.primary_share is not None:
            payload["primary_share"] = round(float(self.primary_share), 4)
        if self.secondary_share is not None:
            payload["secondary_share"] = round(float(self.secondary_share), 4)
        return payload


def _persist_asr_window_result_best_effort(mutator) -> None:
    from backend.core.db import get_sync_session

    session = get_sync_session()
    try:
        mutator(session)
        if hasattr(session, "commit"):
            session.commit()
    except Exception:  # noqa: BLE001
        if hasattr(session, "rollback"):
            session.rollback()
        logger.warning("Failed to persist ASR window result", exc_info=True)
    finally:
        session.close()


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


def _load_recording_audio_window_manifests(session, recording_id: int) -> list[RecordingAudioWindowManifest]:
    if not hasattr(session, "exec"):
        return []

    try:
        return session.exec(
            select(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording_id)
            .order_by(RecordingAudioWindowManifest.window_index)
        ).all()
    except Exception:  # noqa: BLE001
        return []


def _load_recording_audio_chunks(session, recording_id: int) -> list[RecordingAudioChunk]:
    if not hasattr(session, "exec"):
        return []

    try:
        return session.exec(
            select(RecordingAudioChunk)
            .where(RecordingAudioChunk.recording_id == recording_id)
            .order_by(RecordingAudioChunk.sequence_no)
        ).all()
    except Exception:  # noqa: BLE001
        return []


def _ensure_channel_first(audio):
    if audio.ndim == 1:
        return audio.unsqueeze(0)
    return audio


def _expand_audio_channels(audio, target_channel_count: int):
    import torch

    audio = _ensure_channel_first(audio)
    channel_count = int(audio.size(0))
    if channel_count == target_channel_count:
        return audio
    if channel_count == 1 and target_channel_count > 1:
        return audio.repeat(target_channel_count, 1)
    if channel_count > target_channel_count:
        return audio[:target_channel_count]
    padding = audio[-1:, :].repeat(target_channel_count - channel_count, 1)
    return torch.cat([audio, padding], dim=0)


def _concat_live_audio_channels(parts: list):
    import torch

    if not parts:
        return torch.zeros((1, 0))
    normalized_parts = [_ensure_channel_first(part) for part in parts]
    target_channel_count = max(int(part.size(0)) for part in normalized_parts)
    return torch.cat(
        [
            _expand_audio_channels(part, target_channel_count)
            for part in normalized_parts
        ],
        dim=1,
    )


def _mix_live_audio_channels(audio):
    audio = _ensure_channel_first(audio)
    if audio.size(0) == 1:
        return audio.squeeze(0)
    return audio.mean(dim=0)


def _read_live_audio_channels(path: str):
    from backend.processing.vad import safe_read_audio

    return _ensure_channel_first(
        safe_read_audio(path, sampling_rate=LIVE_SAMPLE_RATE, preserve_channels=True)
    )


def _analyze_live_source_channels(audio) -> dict[str, Any]:
    import torch

    audio = _ensure_channel_first(audio)
    channel_count = int(audio.size(0))
    sample_count = int(audio.size(1)) if audio.ndim > 1 else int(audio.numel())
    payload: dict[str, Any] = {
        "channel_count": channel_count,
        "sample_count": sample_count,
    }
    if channel_count < 2 or sample_count <= 0:
        return payload

    rms = torch.sqrt(torch.mean(audio.float() * audio.float(), dim=1) + 1e-12)
    total_rms = float(torch.sum(rms).item())
    if total_rms <= 1e-8:
        payload.update(
            {
                "channel_rms": [0.0 for _ in range(channel_count)],
                "channel_shares": [0.0 for _ in range(channel_count)],
                "source_overlap": False,
            }
        )
        return payload

    shares = [float(value.item()) / total_rms for value in rms]
    primary_channel = int(torch.argmax(rms).item())
    sorted_shares = sorted(shares, reverse=True)
    primary_share = sorted_shares[0]
    secondary_share = sorted_shares[1] if len(sorted_shares) > 1 else 0.0
    primary_source = BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL.get(primary_channel, f"channel_{primary_channel}")
    dominant_source = None
    if primary_share >= LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD and (
        secondary_share <= 0.0
        or primary_share / secondary_share >= LIVE_SOURCE_DOMINANCE_RATIO_THRESHOLD
    ):
        dominant_source = primary_source

    payload.update(
        {
            "channel_rms": [round(float(value.item()), 6) for value in rms],
            "channel_shares": [round(share, 4) for share in shares],
            "primary_channel": primary_channel,
            "primary_source": primary_source,
            "primary_share": round(primary_share, 4),
            "secondary_share": round(secondary_share, 4),
            "dominant_source": dominant_source,
            "source_overlap": bool(secondary_share >= LIVE_SOURCE_OVERLAP_SHARE_THRESHOLD),
        }
    )
    return payload


def _source_channel_speaker_confidence(source_activity: dict[str, Any]) -> float | None:
    if not source_activity.get("dominant_source"):
        return None
    primary_share = float(source_activity.get("primary_share") or 0.0)
    if source_activity.get("source_overlap"):
        return round(min(primary_share, 0.54), 4)
    return round(max(primary_share, LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD), 4)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_live_source_channel_evidence(
    source_channel_evidence: LiveSourceChannelEvidence | dict[str, Any] | None,
) -> LiveSourceChannelEvidence | None:
    if source_channel_evidence is None:
        return None
    if isinstance(source_channel_evidence, LiveSourceChannelEvidence):
        return source_channel_evidence
    if not isinstance(source_channel_evidence, dict):
        return None
    return LiveSourceChannelEvidence(
        dominant_source=source_channel_evidence.get("dominant_source"),
        primary_source=source_channel_evidence.get("primary_source"),
        primary_share=_coerce_float(source_channel_evidence.get("primary_share")),
        secondary_share=_coerce_float(source_channel_evidence.get("secondary_share")),
        source_overlap=bool(source_channel_evidence.get("source_overlap", False)),
        authority=str(source_channel_evidence.get("authority") or LIVE_SOURCE_AUTHORITY_NONE),
        reason=str(source_channel_evidence.get("reason") or "source_channel_payload"),
        preferred_label=source_channel_evidence.get("preferred_label"),
        excluded_labels=tuple(
            str(label)
            for label in source_channel_evidence.get("excluded_labels", [])
            if label
        ),
        speaker_confidence=_coerce_float(source_channel_evidence.get("speaker_confidence")),
    )


def _build_live_source_channel_evidence(
    source_activity: dict[str, Any],
    source_channel_labels: dict[str, str],
) -> LiveSourceChannelEvidence:
    dominant_source = source_activity.get("dominant_source")
    primary_source = source_activity.get("primary_source")
    primary_share = _coerce_float(source_activity.get("primary_share"))
    secondary_share = _coerce_float(source_activity.get("secondary_share"))
    source_overlap = bool(source_activity.get("source_overlap", False))
    speaker_confidence = _source_channel_speaker_confidence(source_activity)

    if source_overlap:
        return LiveSourceChannelEvidence(
            dominant_source=dominant_source,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=True,
            authority=LIVE_SOURCE_AUTHORITY_OVERLAP,
            reason="overlap_reduces_source_authority",
            speaker_confidence=speaker_confidence,
        )

    if not dominant_source:
        return LiveSourceChannelEvidence(
            dominant_source=None,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=False,
            authority=LIVE_SOURCE_AUTHORITY_NONE,
            reason="no_clear_source_dominance",
            speaker_confidence=None,
        )

    if dominant_source == BROWSER_LIVE_MICROPHONE_SOURCE:
        preferred_label = source_channel_labels.get(BROWSER_LIVE_MICROPHONE_SOURCE)
        return LiveSourceChannelEvidence(
            dominant_source=dominant_source,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=False,
            authority=LIVE_SOURCE_AUTHORITY_CLEAR,
            reason=(
                "microphone_dominant_preferred_label"
                if preferred_label
                else "microphone_dominant_assignable"
            ),
            preferred_label=preferred_label,
            speaker_confidence=speaker_confidence,
        )

    if dominant_source == BROWSER_LIVE_SYSTEM_SOURCE:
        microphone_label = source_channel_labels.get(BROWSER_LIVE_MICROPHONE_SOURCE)
        return LiveSourceChannelEvidence(
            dominant_source=dominant_source,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=False,
            authority=LIVE_SOURCE_AUTHORITY_CLEAR,
            reason=(
                "system_dominant_excludes_microphone"
                if microphone_label
                else "system_dominant_no_known_microphone"
            ),
            excluded_labels=(str(microphone_label),) if microphone_label else (),
            speaker_confidence=speaker_confidence,
        )

    return LiveSourceChannelEvidence(
        dominant_source=dominant_source,
        primary_source=primary_source,
        primary_share=primary_share,
        secondary_share=secondary_share,
        source_overlap=False,
        authority=LIVE_SOURCE_AUTHORITY_CLEAR,
        reason="source_dominant",
        speaker_confidence=speaker_confidence,
    )



def _default_live_state() -> dict:
    return {
        "next_expected": LIVE_INITIAL_SEQUENCE,
        "buffer_abs_start": 0.0,
        "last_speaker_label": None,
        _STATE_SOURCE_CHANNEL_LABELS_KEY: {},
        _STATE_SEQUENCE_OUTCOMES_KEY: {},
    }


def _sanitize_source_channel_labels(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    labels: dict[str, str] = {}
    for key, label in value.items():
        source_name = str(key or "").strip()
        speaker_label = str(label or "").strip()
        if source_name and speaker_label:
            labels[source_name] = speaker_label
    return labels


def _sanitize_sequence_outcomes(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}

    outcomes: dict[str, dict[str, Any]] = {}
    for raw_sequence, raw_payload in value.items():
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError):
            continue
        if sequence < LIVE_INITIAL_SEQUENCE or not isinstance(raw_payload, dict):
            continue
        outcome = str(raw_payload.get("outcome") or "").strip()
        if outcome not in _LIVE_SEQUENCE_OUTCOMES:
            continue
        payload: dict[str, Any] = {"outcome": outcome}
        reason = str(raw_payload.get("reason") or "").strip()
        if reason:
            payload["reason"] = reason
        run = raw_payload.get("run")
        if isinstance(run, list):
            cleaned_run = []
            for item in run:
                try:
                    cleaned_run.append(int(item))
                except (TypeError, ValueError):
                    continue
            if cleaned_run:
                payload["run"] = cleaned_run
        error = str(raw_payload.get("error") or "").strip()
        if error:
            payload["error"] = error[:500]
        outcomes[str(sequence)] = payload

    if len(outcomes) <= _MAX_LIVE_SEQUENCE_OUTCOMES:
        return outcomes

    ordered_keys = sorted(outcomes, key=lambda key: int(key))[-_MAX_LIVE_SEQUENCE_OUTCOMES:]
    return {key: outcomes[key] for key in ordered_keys}


def _normalize_live_state(raw_state: dict | None) -> dict:
    default = _default_live_state()
    if not isinstance(raw_state, dict):
        return default

    try:
        next_expected = int(raw_state.get("next_expected", LIVE_INITIAL_SEQUENCE))
    except (TypeError, ValueError):
        next_expected = LIVE_INITIAL_SEQUENCE
    try:
        buffer_abs_start = float(raw_state.get("buffer_abs_start", 0.0))
    except (TypeError, ValueError):
        buffer_abs_start = 0.0

    default.update(
        {
            "next_expected": max(LIVE_INITIAL_SEQUENCE, next_expected),
            "buffer_abs_start": max(0.0, buffer_abs_start),
            "last_speaker_label": raw_state.get("last_speaker_label") or None,
            _STATE_SOURCE_CHANNEL_LABELS_KEY: _sanitize_source_channel_labels(
                raw_state.get(_STATE_SOURCE_CHANNEL_LABELS_KEY)
            ),
            _STATE_SEQUENCE_OUTCOMES_KEY: _sanitize_sequence_outcomes(
                raw_state.get(_STATE_SEQUENCE_OUTCOMES_KEY)
            ),
        }
    )
    return default


def _record_live_sequence_outcome(
    state: dict,
    *,
    sequence: int,
    outcome: str,
    reason: str,
    run: list[int] | None = None,
    error: str | None = None,
) -> None:
    if outcome not in _LIVE_SEQUENCE_OUTCOMES:
        return
    outcomes = _sanitize_sequence_outcomes(state.get(_STATE_SEQUENCE_OUTCOMES_KEY))
    payload: dict[str, Any] = {"outcome": outcome, "reason": reason}
    if run:
        payload["run"] = [int(item) for item in run]
    if error:
        payload["error"] = str(error).strip()[:500]
    outcomes[str(int(sequence))] = payload
    state[_STATE_SEQUENCE_OUTCOMES_KEY] = _sanitize_sequence_outcomes(outcomes)


def _record_live_sequence_outcome_metric(
    *,
    recording_id: int,
    sequence: int,
    outcome: str,
    reason: str,
    run: list[int] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "sequence": int(sequence),
        "outcome": outcome,
        "reason": reason,
        "is_first_sequence": int(sequence) == LIVE_INITIAL_SEQUENCE,
    }
    if run:
        payload["run"] = [int(item) for item in run]
    if extra_payload:
        payload.update(extra_payload)
    record_pipeline_metric(
        stage="live_sequence_outcome",
        recording_id=recording_id,
        payload=payload,
        status="error" if outcome == "failed" else outcome,
        log=logger,
    )


def _write_live_state_best_effort(live_dir, state: dict) -> None:
    try:
        write_live_state(live_dir, state)
    except OSError:
        logger.warning("Failed to persist live state", exc_info=True)


def read_live_state(live_dir) -> dict:
    """Read the live lane state, returning defaults when absent or unreadable."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_live_state(data)
    except (FileNotFoundError, TypeError, ValueError, OSError):
        return _default_live_state()


def write_live_state(live_dir, state: dict) -> None:
    """Persist the live lane state to disk."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    normalized_state = _normalize_live_state(state)
    state_payload = {
        "next_expected": int(normalized_state["next_expected"]),
        "buffer_abs_start": float(normalized_state["buffer_abs_start"]),
    }
    if normalized_state.get("last_speaker_label"):
        state_payload["last_speaker_label"] = normalized_state["last_speaker_label"]
    if normalized_state.get(_STATE_SOURCE_CHANNEL_LABELS_KEY):
        state_payload[_STATE_SOURCE_CHANNEL_LABELS_KEY] = normalized_state[
            _STATE_SOURCE_CHANNEL_LABELS_KEY
        ]
    if normalized_state.get(_STATE_SEQUENCE_OUTCOMES_KEY):
        state_payload[_STATE_SEQUENCE_OUTCOMES_KEY] = normalized_state[
            _STATE_SEQUENCE_OUTCOMES_KEY
        ]
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state_payload, f)


def classify_speech(
    speech: list[dict],
    combined_len: float,
    *,
    forced_max_s: float = DEFAULT_FORCED_MAX,
    max_segment_s: float = DEFAULT_MAX_SEGMENT_S,
) -> tuple[list[dict], float]:
    """Split detected speech regions into completed regions and a carry-over cut point.

    Returns (complete_segments, cut_point) where cut_point is the buffer offset
    (seconds) from which unconsumed audio should be carried into the next run.
    """
    if not speech:
        # No speech: drop the silent buffer entirely.
        return [], combined_len

    last = speech[-1]
    trailing_incomplete = last["end"] >= combined_len - TRAIL_EPS

    if trailing_incomplete and (combined_len - last["start"]) >= forced_max_s:
        # Trailing utterance has run too long; treat it as complete now.
        complete_regions = speech
        cut_point = combined_len
    elif trailing_incomplete:
        # Carry the trailing utterance forward from its start.
        complete_regions = speech[:-1]
        cut_point = last["start"]
    else:
        # Last region ended with silence; everything is complete.
        complete_regions = speech
        cut_point = last["end"]

    return _split_complete_regions(complete_regions, max_segment_s=max_segment_s), cut_point


def _split_complete_regions(
    regions: list[dict],
    *,
    max_segment_s: float,
) -> list[dict]:
    if max_segment_s <= 0:
        return regions

    split_regions: list[dict] = []
    for region in regions:
        split_regions.extend(_split_region(region, max_segment_s=max_segment_s))
    return split_regions


def _split_region(region: dict, *, max_segment_s: float) -> list[dict]:
    start = float(region["start"])
    end = float(region["end"])
    duration = end - start

    if duration <= max_segment_s:
        return [region]

    split_regions: list[dict] = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(end, chunk_start + max_segment_s)
        chunk = dict(region)
        chunk["start"] = chunk_start
        chunk["end"] = chunk_end
        split_regions.append(chunk)
        chunk_start = chunk_end

    return split_regions


def _build_live_config() -> dict:
    """Build a minimal config dict for the live transcription engine call."""
    backend = config_manager.get("transcription_backend", "whisper")
    return {
        "transcription_backend": backend,
        "parakeet_model": config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3"),
        "whisper_model_size": config_manager.get("whisper_model_size", "turbo"),
        "processing_device": config_manager.get("processing_device", "auto"),
        "context_window_s": config_manager.get("live_context_window_s", 5.0),
        "forced_max_s": config_manager.get("live_forced_max_s", DEFAULT_FORCED_MAX),
        "max_segment_s": config_manager.get("live_max_segment_s", DEFAULT_MAX_SEGMENT_S),
        "speech_pad_ms": config_manager.get("live_speech_pad_ms", 300),
    }


def _get_speaker_display_name(speaker: Any) -> str:
    global_speaker = getattr(speaker, "global_speaker", None)
    return (
        getattr(speaker, "local_name", None)
        or (getattr(global_speaker, "name", None) if global_speaker else None)
        or getattr(speaker, "name", None)
        or "Speaker 1"
    )


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
        " ".join(str(segment.get("text", "") or "").strip() for segment in kept_segments),
    ).strip()


def _extract_region_segment_payloads(result: dict, prefix_s: float) -> list[dict[str, Any]]:
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
        if end <= prefix_s + EPS:
            # Pure context: entirely within the run-up.
            continue
        if start >= prefix_s - EPS:
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
                if word_end <= prefix_s + EPS:
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
            "start_ms": int(region_start_ms + round(float(segment.get("start", 0.0)) * 1000.0)),
            "end_ms": int(region_start_ms + round(float(segment.get("end", 0.0)) * 1000.0)),
            "text": str(segment.get("text", "") or ""),
        }
        words = []
        for word in segment.get("words") or []:
            word_text = str(word.get("word") or "").strip()
            if not word_text:
                continue
            words.append(
                {
                    "start_ms": int(region_start_ms + round(float(word.get("start", 0.0)) * 1000.0)),
                    "end_ms": int(region_start_ms + round(float(word.get("end", 0.0)) * 1000.0)),
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


@celery_app.task(bind=True)
def transcribe_segment_live_task(self, recording_id: int, sequence: int):
    """Transcribe an uploaded recording segment in the live lane.

    Sequence-gated: only the task holding next_expected drains the contiguous
    run of segments on disk. Any failure is logged and the lane still advances;
    the final processing pipeline recovers everything.
    """
    import torch

    from backend.core.db import get_sync_session
    from backend.models.recording import Recording, RecordingStatus
    from backend.models.user import User
    from backend.processing.vad import detect_speech_segments
    from backend.processing.transcribe import transcribe_audio
    from backend.worker.tasks import resolve_llm_config

    config_manager.reload()
    record_pipeline_metric(
        stage="live_task_started",
        recording_id=recording_id,
        payload={"sequence": sequence},
        log=logger,
    )

    # The live lane is still meaningful for already-uploaded tail segments
    # while a recording is paused. Once finalize() or a terminal state flips
    # the recording out of the in-flight states, queued live work must stop
    # even though uploaded chunk files may remain on disk until lifecycle
    # cleanup. The final pipeline is authoritative after that status flip.
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if not recording or recording.status not in {
            RecordingStatus.UPLOADING,
            RecordingStatus.PAUSED,
        }:
            record_pipeline_metric(
                stage="live_task_skipped",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "reason": "not_uploading",
                    "status": getattr(recording, "status", None),
                },
                status="skipped",
                log=logger,
            )
            _record_live_sequence_outcome_metric(
                recording_id=recording_id,
                sequence=sequence,
                outcome="skipped",
                reason="not_uploading",
                extra_payload={"status": getattr(recording, "status", None)},
            )
            return
    finally:
        session.close()

    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        record_pipeline_metric(
            stage="live_task_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "reason": "upload_buffer_missing",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="skipped",
            reason="upload_buffer_missing",
        )
        return
    live_dir = temp_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    state = read_live_state(live_dir)
    source_channel_labels = state[_STATE_SOURCE_CHANNEL_LABELS_KEY]
    session = get_sync_session()
    try:
        manifest_rows = _load_recording_audio_window_manifests(session, recording_id)
        resumed_state = infer_resume_state_from_manifests(manifest_rows)
        if resumed_state and int(resumed_state["next_expected"]) > int(state["next_expected"]):
            state["next_expected"] = int(resumed_state["next_expected"])
            state["buffer_abs_start"] = float(resumed_state["buffer_abs_start"])
            record_pipeline_metric(
                stage="live_state_resumed_from_manifest",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "next_expected": state["next_expected"],
                    "buffer_abs_start": round(float(state["buffer_abs_start"]), 3),
                },
                log=logger,
            )
    finally:
        session.close()

    next_expected = state["next_expected"]
    buffer_abs_start = state["buffer_abs_start"]

    # --- Gating ---
    if sequence < next_expected:
        # Already consumed by an earlier run.
        _record_live_sequence_outcome(
            state,
            sequence=sequence,
            outcome="skipped",
            reason="already_consumed",
        )
        _write_live_state_best_effort(live_dir, state)
        record_pipeline_metric(
            stage="live_sequence_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "next_expected": next_expected,
                "reason": "already_consumed",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="skipped",
            reason="already_consumed",
            extra_payload={"next_expected": next_expected},
        )
        return
    if sequence > next_expected:
        # Gap: this segment waits on disk until the run reaches it.
        _record_live_sequence_outcome(
            state,
            sequence=sequence,
            outcome="deferred",
            reason="waiting_for_gap",
        )
        _write_live_state_best_effort(live_dir, state)
        record_pipeline_metric(
            stage="live_sequence_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "next_expected": next_expected,
                "reason": "waiting_for_gap",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="deferred",
            reason="waiting_for_gap",
            extra_payload={"next_expected": next_expected},
        )
        return

    # --- Drain: contiguous run starting at next_expected ---
    run = []
    n = next_expected
    while os.path.exists(str(temp_dir / f"{n}.wav")):
        run.append(n)
        n += 1
    if not run:
        # Defensive: the triggering segment should exist; nothing to do.
        _record_live_sequence_outcome(
            state,
            sequence=sequence,
            outcome="skipped",
            reason="triggering_segment_missing",
        )
        _write_live_state_best_effort(live_dir, state)
        record_pipeline_metric(
            stage="live_sequence_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "next_expected": next_expected,
                "reason": "triggering_segment_missing",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="skipped",
            reason="triggering_segment_missing",
            extra_payload={"next_expected": next_expected},
        )
        return

    buffer_path = str(live_dir / _BUFFER_FILENAME)

    try:
        record_pipeline_metric(
            stage="live_run_started",
            recording_id=recording_id,
            payload={"sequence": sequence, "run": run},
            log=logger,
        )
        # --- Build combined buffer ---
        channel_parts = []
        if os.path.exists(buffer_path):
            channel_parts.append(_read_live_audio_channels(buffer_path))
        for seg_n in run:
            channel_parts.append(_read_live_audio_channels(str(temp_dir / f"{seg_n}.wav")))

        combined_channels = _concat_live_audio_channels(channel_parts)
        combined = _mix_live_audio_channels(combined_channels)
        combined_len = combined.numel() / LIVE_SAMPLE_RATE
        combined_abs_start = buffer_abs_start

        # --- Build the live engine config (needed before the VAD call) ---
        live_config = _build_live_config()
        W = int(live_config["context_window_s"] * LIVE_SAMPLE_RATE)

        # --- Load user-aware config once for live speaker matching ---
        merged_config = live_config
        user_id = None
        session = get_sync_session()
        try:
            recording = session.get(Recording, recording_id)
            if recording:
                user_id = getattr(recording, "user_id", None)
                user_settings = {}
                if user_id:
                    user = session.get(User, user_id)
                    if user and user.settings:
                        user_settings = user.settings
                if hasattr(session, "exec"):
                    merged_config = resolve_llm_config(session, user_settings).merged_config
                merged_config.setdefault("transcription_backend", live_config["transcription_backend"])
                live_config.update(
                    {
                        "transcription_backend": merged_config.get(
                            "transcription_backend",
                            live_config["transcription_backend"],
                        ),
                        "parakeet_model": merged_config.get(
                            "parakeet_model",
                            live_config["parakeet_model"],
                        ),
                        "whisper_model_size": merged_config.get(
                            "whisper_model_size",
                            live_config["whisper_model_size"],
                        ),
                        "processing_device": merged_config.get(
                            "processing_device",
                            live_config["processing_device"],
                        ),
                        "forced_max_s": merged_config.get(
                            "live_forced_max_s",
                            live_config["forced_max_s"],
                        ),
                        "max_segment_s": merged_config.get(
                            "live_max_segment_s",
                            live_config["max_segment_s"],
                        ),
                    }
                )
        finally:
            session.close()

        # --- Detect speech and classify ---
        speech = detect_speech_segments(
            combined,
            min_silence_duration_ms=LIVE_MIN_SILENCE_MS,
            speech_pad_ms=live_config["speech_pad_ms"],
        )
        complete, cut_point = classify_speech(
            speech,
            combined_len,
            forced_max_s=float(live_config["forced_max_s"]),
            max_segment_s=float(live_config["max_segment_s"]),
        )
        record_pipeline_metric(
            stage="live_vad_classified",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "run": run,
                "speech_count": len(speech),
                "complete_count": len(complete),
                "combined_len_s": round(combined_len, 3),
                "cut_point_s": round(cut_point, 3),
            },
            log=logger,
        )

        # --- Read the rolling left-context buffer (already-consumed audio) ---
        context_path = str(live_dir / _CONTEXT_FILENAME)
        if os.path.exists(context_path):
            prev_context_channels = _read_live_audio_channels(context_path)
        else:
            prev_context_channels = torch.zeros((combined_channels.size(0), 0))
        prev_context_channels = _expand_audio_channels(
            prev_context_channels,
            int(combined_channels.size(0)),
        )
        prev_context = _mix_live_audio_channels(prev_context_channels)

        # --- Transcribe each completed speech region ---
        new_segments = []
        pending_asr_completions: list[dict[str, Any]] = []
        live_config_hash = build_recording_asr_window_result_config_hash(live_config)
        live_model_name = get_transcription_model_name(live_config)
        for sp in complete:
            start_sample = int(sp["start"] * LIVE_SAMPLE_RATE)
            end_sample = int(sp["end"] * LIVE_SAMPLE_RATE)
            region = combined[start_sample:end_sample]
            region_channels = combined_channels[:, start_sample:end_sample]
            if region.numel() == 0:
                continue
            source_activity = _analyze_live_source_channels(region_channels)
            source_channel_evidence = _build_live_source_channel_evidence(
                source_activity,
                source_channel_labels,
            )

            # Prepend a rolling audio context window so the engine has run-up.
            left_context = torch.cat([prev_context, combined[:start_sample]])
            if W > 0:
                left_context = left_context[-W:]
            else:
                left_context = left_context[:0]
            clip = torch.cat([left_context, region])
            prefix_s = left_context.numel() / LIVE_SAMPLE_RATE
            region_start_ms = int(round((combined_abs_start + sp["start"]) * 1000.0))
            region_end_ms = int(round((combined_abs_start + sp["end"]) * 1000.0))
            ledger_enabled = bool(config_manager.get("enable_asr_window_result_ledger", True))

            if ledger_enabled:
                _persist_asr_window_result_best_effort(
                    lambda ledger_session: start_recording_asr_window_result(
                        ledger_session,
                        recording_id=recording_id,
                        source_kind="live",
                        span_start_ms=region_start_ms,
                        span_end_ms=region_end_ms,
                        chunk_start_sequence=run[0],
                        chunk_end_sequence=run[-1],
                        config=live_config,
                    )
                )

            clip_path = str(live_dir / "clip.wav")
            try:
                import silero_vad

                tensor = clip if clip.ndim > 1 else clip.unsqueeze(0)
                silero_vad.save_audio(clip_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
                with pipeline_metric_timer(
                    stage="live_asr_region",
                    recording_id=recording_id,
                    payload={
                        "sequence": sequence,
                        "region_start_s": round(combined_abs_start + sp["start"], 3),
                        "region_end_s": round(combined_abs_start + sp["end"], 3),
                        "prefix_s": round(prefix_s, 3),
                        "engine": live_config.get("transcription_backend"),
                    },
                    log=logger,
                ) as metric:
                    try:
                        result = transcribe_audio(clip_path, config=live_config)
                    except Exception as exc:
                        if ledger_enabled:
                            _persist_asr_window_result_best_effort(
                                lambda ledger_session: fail_recording_asr_window_result(
                                    ledger_session,
                                    recording_id=recording_id,
                                    source_kind="live",
                                    span_start_ms=region_start_ms,
                                    span_end_ms=region_end_ms,
                                    chunk_start_sequence=run[0],
                                    chunk_end_sequence=run[-1],
                                    config=live_config,
                                    error_summary=str(exc).strip()[:500] or "Live ASR invocation failed.",
                                    error_payload={"error_type": exc.__class__.__name__},
                                )
                            )
                        raise
                    metric["payload"]["text_chars"] = len((result or {}).get("text") or "")
                speaker_label = "UNKNOWN"

            finally:
                if os.path.exists(clip_path):
                    try:
                        os.remove(clip_path)
                    except OSError:
                        pass

            if not result:
                if ledger_enabled:
                    _persist_asr_window_result_best_effort(
                        lambda ledger_session: fail_recording_asr_window_result(
                            ledger_session,
                            recording_id=recording_id,
                            source_kind="live",
                            span_start_ms=region_start_ms,
                            span_end_ms=region_end_ms,
                            chunk_start_sequence=run[0],
                            chunk_end_sequence=run[-1],
                            config=live_config,
                            error_summary="Live ASR returned no result.",
                            error_payload={"error_type": "empty_result"},
                        )
                    )
                continue
            region_segment_payloads = _extract_region_segment_payloads(result, prefix_s)
            text = _strip_repetition(_extract_region_text(result, prefix_s))
            if not text:
                if ledger_enabled:
                    _persist_asr_window_result_best_effort(
                        lambda ledger_session: fail_recording_asr_window_result(
                            ledger_session,
                            recording_id=recording_id,
                            source_kind="live",
                            span_start_ms=region_start_ms,
                            span_end_ms=region_end_ms,
                            chunk_start_sequence=run[0],
                            chunk_end_sequence=run[-1],
                            config=live_config,
                            error_summary="Live ASR produced no emitted text.",
                            error_payload={"error_type": "empty_emitted_text"},
                        )
                    )
                continue

            segment_public_id = _build_live_utterance_public_id(
                recording_id=recording_id,
                span_start_ms=region_start_ms,
                span_end_ms=region_end_ms,
                speaker_label=speaker_label,
                text=text,
            )

            new_segments.append(
                {
                    "id": segment_public_id,
                    "start": combined_abs_start + sp["start"],
                    "end": combined_abs_start + sp["end"],
                    "speaker": speaker_label,
                    "text": text,
                    "provisional": True,
                    "segment_source": "live",
                    "speaker_state": "provisional",
                    "speaker_confidence": source_channel_evidence.speaker_confidence,
                    "confidence_payload": _build_live_confidence_payload(
                        region_segment_payloads=region_segment_payloads,
                        region_start_ms=region_start_ms,
                        region_end_ms=region_end_ms,
                        source_activity=source_activity,
                        source_channel_evidence=source_channel_evidence,
                    ),
                }
            )
            pending_asr_completions.append(
                {
                    "public_id": segment_public_id,
                    "span_start_ms": region_start_ms,
                    "span_end_ms": region_end_ms,
                    "result_payload": {
                        "sequence": sequence,
                        "run": list(run),
                        "segment_count": len((result or {}).get("segments", [])),
                        "text_chars": len((result or {}).get("text") or ""),
                        "emitted_text_chars": len(text or ""),
                        "prefix_ms": int(round(prefix_s * 1000.0)),
                    },
                }
            )
            if speaker_label != "UNKNOWN":
                state["last_speaker_label"] = speaker_label
                if (
                    source_channel_evidence.authority == LIVE_SOURCE_AUTHORITY_CLEAR
                    and source_channel_evidence.dominant_source == BROWSER_LIVE_MICROPHONE_SOURCE
                ):
                    source_channel_labels.setdefault(BROWSER_LIVE_MICROPHONE_SOURCE, speaker_label)

        # --- Carry over the unconsumed trailing audio ---
        cut_sample = int(cut_point * LIVE_SAMPLE_RATE)
        new_buffer_channels = combined_channels[:, cut_sample:]
        if new_buffer_channels.numel() > 0:
            tensor = new_buffer_channels if new_buffer_channels.ndim > 1 else new_buffer_channels.unsqueeze(0)
            import silero_vad

            silero_vad.save_audio(buffer_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
        elif os.path.exists(buffer_path):
            try:
                os.remove(buffer_path)
            except OSError:
                pass
        new_abs_start = combined_abs_start + cut_point

        # --- Update the rolling left-context buffer ---
        # consumed = the already-consumed audio immediately preceding the new
        # buffer; its last W samples become run-up for the next run.
        consumed_channels = _concat_live_audio_channels(
            [prev_context_channels, combined_channels[:, :cut_sample]]
        )
        if W > 0:
            consumed_channels = consumed_channels[:, -W:]
        else:
            consumed_channels = consumed_channels[:, :0]
        if consumed_channels.numel() > 0:
            tensor = consumed_channels if consumed_channels.ndim > 1 else consumed_channels.unsqueeze(0)
            import silero_vad

            silero_vad.save_audio(context_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
        elif os.path.exists(context_path):
            try:
                os.remove(context_path)
            except OSError:
                pass

        # --- Persist provisional segments and processed manifest coverage ---
        should_dispatch_meeting_edge = False
        session = get_sync_session()
        try:
            recording = session.get(Recording, recording_id)
            if recording and recording.status in {
                RecordingStatus.UPLOADING,
                RecordingStatus.PAUSED,
            }:
                if new_segments:
                    created_utterances = []
                    created_public_ids: set[str] = set()
                    use_canonical_live_writes = bool(
                        config_manager.get("enable_canonical_transcript_writes", True)
                    ) and hasattr(session, "exec") and hasattr(session, "execute")

                    if use_canonical_live_writes:
                        created_utterances = append_utterances_from_segments(
                            session,
                            recording_id=recording_id,
                            segments=new_segments,
                            run_kind=ProcessingRunKind.LIVE,
                            source="live",
                            state_override=TranscriptUtteranceState.PROVISIONAL,
                            trigger_source="worker",
                            config_hash=live_config_hash,
                            transcription_backend=str(live_config.get("transcription_backend") or "whisper"),
                            model_metadata={
                                "model_name": live_model_name,
                                "chunk_start_sequence": run[0],
                                "chunk_end_sequence": run[-1],
                                "trigger_sequence": sequence,
                            },
                            span_start_ms=min(item["span_start_ms"] for item in pending_asr_completions),
                            span_end_ms=max(item["span_end_ms"] for item in pending_asr_completions),
                        )
                    else:
                        from sqlalchemy.orm.attributes import flag_modified

                        transcript = recording.transcript
                        if transcript is not None:
                            transcript.segments = (transcript.segments or []) + new_segments
                            flag_modified(transcript, "segments")
                            session.add(transcript)

                    created_public_ids = {utterance.public_id for utterance in created_utterances}

                    user_settings = getattr(
                        getattr(recording, "user", None),
                        "settings",
                        None,
                    )
                    if user_settings is None and getattr(recording, "user_id", None):
                        from backend.models.user import User

                        user = session.get(User, recording.user_id)
                        user_settings = getattr(user, "settings", None) if user else None

                    should_dispatch_meeting_edge = is_meeting_edge_enabled(user_settings)

                manifest_rows = _load_recording_audio_window_manifests(session, recording_id)
                updated_manifest_rows = mark_audio_windows_processed(
                    manifest_rows,
                    up_to_sequence=run[-1],
                    status=WINDOW_STATUS_LIVE_PROCESSED,
                )
                for manifest_row in updated_manifest_rows:
                    session.add(manifest_row)

                session.commit()

                if new_segments and ledger_enabled:
                    for pending_result in pending_asr_completions:
                        produced_ids = (
                            [pending_result["public_id"]]
                            if pending_result["public_id"] in created_public_ids
                            else None
                        )
                        _persist_asr_window_result_best_effort(
                            lambda ledger_session, pending_result=pending_result, produced_ids=produced_ids: complete_recording_asr_window_result(
                                ledger_session,
                                recording_id=recording_id,
                                source_kind="live",
                                span_start_ms=pending_result["span_start_ms"],
                                span_end_ms=pending_result["span_end_ms"],
                                chunk_start_sequence=run[0],
                                chunk_end_sequence=run[-1],
                                config=live_config,
                                config_hash=live_config_hash,
                                result_payload=pending_result["result_payload"],
                                produced_utterance_public_ids=produced_ids,
                            )
                        )

                if new_segments:
                    record_pipeline_metric(
                        stage="live_segments_persisted",
                        recording_id=recording_id,
                        payload={
                            "sequence": sequence,
                            "segment_count": len(new_segments),
                            "first_segment_start_s": round(
                                min(segment["start"] for segment in new_segments),
                                3,
                            ),
                            "last_segment_end_s": round(
                                max(segment["end"] for segment in new_segments),
                                3,
                            ),
                            "last_speaker_label": state.get("last_speaker_label"),
                        },
                        log=logger,
                    )
        finally:
            session.close()



            if should_dispatch_meeting_edge:
                try:
                    from backend.worker.tasks import refresh_meeting_edge_task

                    refresh_meeting_edge_task.delay(recording_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to dispatch Meeting Edge refresh for recording %s: %s",
                        recording_id,
                        exc,
                    )

        # --- Advance the lane ---
        for consumed_sequence in run:
            _record_live_sequence_outcome(
                state,
                sequence=consumed_sequence,
                outcome="consumed",
                reason="live_run_completed",
                run=run,
            )
        state["next_expected"] = run[-1] + 1
        state["buffer_abs_start"] = new_abs_start
        write_live_state(live_dir, state)
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="consumed",
            reason="live_run_completed",
            run=run,
            extra_payload={"next_expected": state["next_expected"]},
        )
        record_pipeline_metric(
            stage="live_run_completed",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "run": run,
                "new_segments": len(new_segments),
                "next_expected": state["next_expected"],
                "buffer_abs_start": round(new_abs_start, 3),
            },
            log=logger,
        )

    except Exception as exc:
        record_pipeline_metric(
            stage="live_run_failed",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "run": run,
                "error": str(exc),
                "catch_up_recoverable": bool(run),
            },
            status="error",
            log=logger,
        )
        # Non-fatal: log and keep the source windows discoverable for final
        # catch-up through their pending ASR coverage.
        logger.error(
            "Live transcription failed for recording %s run %s: %s",
            recording_id,
            run,
            exc,
            exc_info=True,
        )
        if 'pending_asr_completions' in locals() and config_manager.get("enable_asr_window_result_ledger", True):
            for pending_result in pending_asr_completions:
                _persist_asr_window_result_best_effort(
                    lambda ledger_session, pending_result=pending_result: fail_recording_asr_window_result(
                        ledger_session,
                        recording_id=recording_id,
                        source_kind="live",
                        span_start_ms=pending_result["span_start_ms"],
                        span_end_ms=pending_result["span_end_ms"],
                        chunk_start_sequence=run[0] if 'run' in locals() and run else None,
                        chunk_end_sequence=run[-1] if 'run' in locals() and run else None,
                        config=live_config if 'live_config' in locals() else None,
                        error_summary=str(exc).strip()[:500] or "Live utterance persistence failed.",
                        error_payload={"error_type": exc.__class__.__name__},
                    )
                )
        if run:
            for failed_sequence in run:
                _record_live_sequence_outcome(
                    state,
                    sequence=failed_sequence,
                    outcome="failed",
                    reason="live_run_failed",
                    run=run,
                    error=str(exc),
                )
            state["next_expected"] = run[-1] + 1
            _write_live_state_best_effort(live_dir, state)
            _record_live_sequence_outcome_metric(
                recording_id=recording_id,
                sequence=sequence,
                outcome="failed",
                reason="live_run_failed",
                run=run,
                extra_payload={
                    "error": str(exc),
                    "next_expected": state["next_expected"],
                    "catch_up_recoverable": True,
                },
            )
