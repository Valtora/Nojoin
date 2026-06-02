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
    rolling_diarization_window_timer,
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
    ensure_processing_run,
    reconcile_diarization_window_result,
)
from backend.utils.config_manager import config_manager, is_meeting_edge_enabled
from backend.utils.recording_storage import recording_upload_temp_dir
from backend.utils.rolling_diarization import (
    build_rolling_diarization_config_hash,
    analyze_window_speakers,
    get_rolling_diarization_model_name,
    persist_diarization_window_result,
)

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


def _build_audio_window_clip(
    *,
    manifest_row: RecordingAudioWindowManifest,
    chunk_rows: list[RecordingAudioChunk],
    clip_path: str,
) -> bool:
    import silero_vad

    from backend.processing.vad import safe_read_audio

    window_start_s = float(manifest_row.window_start_ms) / 1000.0
    window_end_s = float(manifest_row.window_end_ms) / 1000.0
    relevant_chunks = [
        row
        for row in chunk_rows
        if int(row.sequence_no) >= int(manifest_row.chunk_start_sequence)
        and int(row.sequence_no) <= int(manifest_row.chunk_end_sequence)
    ]
    if not relevant_chunks:
        return False

    parts = []
    for chunk_row in relevant_chunks:
        chunk_audio = safe_read_audio(
            str(chunk_row.storage_path),
            sampling_rate=LIVE_SAMPLE_RATE,
            preserve_channels=True,
        )
        chunk_start_s = float(chunk_row.absolute_start_ms) / 1000.0
        chunk_end_s = float(chunk_row.absolute_end_ms) / 1000.0
        overlap_start_s = max(window_start_s, chunk_start_s)
        overlap_end_s = min(window_end_s, chunk_end_s)
        if overlap_end_s <= overlap_start_s:
            continue
        start_sample = int(round((overlap_start_s - chunk_start_s) * LIVE_SAMPLE_RATE))
        end_sample = int(round((overlap_end_s - chunk_start_s) * LIVE_SAMPLE_RATE))
        if end_sample <= start_sample:
            continue
        chunk_audio = _ensure_channel_first(chunk_audio)
        parts.append(chunk_audio[:, start_sample:end_sample])

    if not parts:
        return False

    clip_audio = _concat_live_audio_channels(parts)
    clip_tensor = clip_audio if clip_audio.ndim > 1 else clip_audio.unsqueeze(0)
    silero_vad.save_audio(clip_path, clip_tensor, sampling_rate=LIVE_SAMPLE_RATE)
    return True


def _select_live_rolling_diarization_manifests(
    session,
    *,
    recording_id: int,
    up_to_sequence: int,
    config_hash: str,
    max_windows_per_pass: int,
    manifest_rows: list[RecordingAudioWindowManifest] | None = None,
    processing_run_status_by_id: dict[int, str] | None = None,
) -> list[RecordingAudioWindowManifest]:
    manifest_rows = list(manifest_rows) if manifest_rows is not None else _load_recording_audio_window_manifests(session, recording_id)
    completed_window_indexes = {
        int(window_index)
        for window_index in session.exec(
            select(DiarizationWindowResult.window_index)
            .where(DiarizationWindowResult.recording_id == recording_id)
            .where(DiarizationWindowResult.config_hash == config_hash)
            .where(DiarizationWindowResult.status == "completed")
        ).all()
    }

    processing_run_status_by_id = dict(processing_run_status_by_id or {})
    if not processing_run_status_by_id:
        processing_run_ids = {
            int(row.diarization_processing_run_id)
            for row in manifest_rows
            if getattr(row, "diarization_processing_run_id", None) is not None
        }
        processing_run_status_by_id = _load_live_rolling_processing_run_statuses(
            session,
            processing_run_ids=processing_run_ids,
        )

    eligible_rows = [
        row
        for row in manifest_rows
        if row.id is not None
        and int(row.chunk_end_sequence) <= int(up_to_sequence)
        and (not bool(row.is_partial) or bool(row.is_sealed))
        and window_asr_is_processed(row)
        and int(row.window_index) not in completed_window_indexes
        and _live_rolling_manifest_is_claimable(
            row,
            processing_run_status_by_id=processing_run_status_by_id,
        )
    ]
    eligible_rows.sort(key=lambda row: int(row.window_index))
    return eligible_rows[: max(int(max_windows_per_pass), 1)]


def _live_rolling_manifest_is_claimable(
    manifest_row,
    *,
    processing_run_status_by_id: dict[int, str],
) -> bool:
    status_value = window_diarization_status(manifest_row)
    if status_value != WINDOW_DIARIZATION_STATUS_PROCESSING:
        return True

    processing_run_id = getattr(manifest_row, "diarization_processing_run_id", None)
    if processing_run_id is None:
        return True

    run_status = processing_run_status_by_id.get(int(processing_run_id))
    return run_status in {
        None,
        ProcessingRunStatus.COMPLETED.value,
        ProcessingRunStatus.FAILED.value,
        ProcessingRunStatus.CANCELLED.value,
    }


def _load_live_rolling_processing_run_statuses(
    session,
    *,
    processing_run_ids: set[int],
) -> dict[int, str]:
    if not processing_run_ids or not hasattr(session, "exec"):
        return {}

    try:
        rows = session.exec(
            select(ProcessingRun.id, ProcessingRun.status).where(
                ProcessingRun.id.in_(sorted(processing_run_ids))
            )
        ).all()
    except Exception:  # noqa: BLE001
        return {}

    return {
        int(run_id): (status.value if hasattr(status, "value") else str(status))
        for run_id, status in rows
    }


def _load_lockable_live_rolling_diarization_manifests(
    session,
    *,
    recording_id: int,
) -> list[RecordingAudioWindowManifest]:
    if not hasattr(session, "exec"):
        return _load_recording_audio_window_manifests(session, recording_id)

    try:
        return session.exec(
            select(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording_id)
            .order_by(RecordingAudioWindowManifest.window_index)
            .with_for_update(skip_locked=True)
        ).all()
    except Exception:  # noqa: BLE001
        return _load_recording_audio_window_manifests(session, recording_id)


def _claim_live_rolling_diarization_manifests(
    session,
    *,
    recording_id: int,
    up_to_sequence: int,
    config_hash: str,
    max_windows_per_pass: int,
    processing_run_id: int | None = None,
) -> list[RecordingAudioWindowManifest]:
    manifest_rows = _load_lockable_live_rolling_diarization_manifests(
        session,
        recording_id=recording_id,
    )
    processing_run_status_by_id = _load_live_rolling_processing_run_statuses(
        session,
        processing_run_ids={
                int(row.diarization_processing_run_id)
            for row in manifest_rows
                if getattr(row, "diarization_processing_run_id", None) is not None
        },
    )
    claimed_rows = _select_live_rolling_diarization_manifests(
        session,
        recording_id=recording_id,
        up_to_sequence=up_to_sequence,
        config_hash=config_hash,
        max_windows_per_pass=max_windows_per_pass,
        manifest_rows=manifest_rows,
        processing_run_status_by_id=processing_run_status_by_id,
    )

    for manifest_row in claimed_rows:
        manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_PROCESSING
        manifest_row.diarization_processing_run_id = processing_run_id
        manifest_row.diarization_config_hash = config_hash
        manifest_row.diarization_window_result_id = None
        manifest_row.diarization_last_error = None
        manifest_row.status = WINDOW_STATUS_LIVE_PROCESSING
        manifest_row.processing_run_id = processing_run_id
        manifest_row.last_error = None
        session.add(manifest_row)

    return claimed_rows


def _count_active_live_rolling_diarization_runs(session) -> int:
    if not hasattr(session, "exec"):
        return 0

    from backend.utils.time import utc_now

    stale_cutoff = utc_now() - timedelta(minutes=15)
    try:
        return len(
            session.exec(
                select(ProcessingRun.id)
                .where(ProcessingRun.run_kind == ProcessingRunKind.ROLLING_DIARIZATION)
                .where(ProcessingRun.status == ProcessingRunStatus.RUNNING)
                .where(
                    or_(
                        ProcessingRun.updated_at.is_(None),
                        ProcessingRun.updated_at >= stale_cutoff,
                    )
                )
            ).all()
        )
    except Exception:  # noqa: BLE001
        if hasattr(session, "rollback"):
            session.rollback()
        logger.warning(
            "Failed to count active live rolling diarization runs",
            exc_info=True,
        )
        return 0


def _run_live_rolling_diarization_pass(
    *,
    recording_id: int,
    up_to_sequence: int,
    user_id: int | None,
    merged_config: dict[str, Any],
    live_dir,
) -> dict[str, int]:
    from backend.core.db import get_sync_session
    from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
    from backend.processing.diarize import diarize_audio
    from backend.utils.time import utc_now

    summary = {
        "processed_window_count": 0,
        "matched_turn_count": 0,
        "updated_utterance_count": 0,
        "preserved_manual_lock_count": 0,
        "voiceprint_update_count": 0,
        "global_voiceprint_update_count": 0,
    }
    if not config_manager.get("enable_rolling_diarization", True):
        return summary
    if not bool(merged_config.get("enable_diarization", True)):
        return summary

    rolling_run = None
    temp_clip_paths: list[str] = []
    session = get_sync_session()
    try:
        target_window_ms = int(config_manager.get("rolling_diarization_window_ms", 20_000))
        hop_ms = int(config_manager.get("rolling_diarization_hop_ms", 5_000))
        config_hash = build_rolling_diarization_config_hash(
            merged_config,
            target_window_ms=target_window_ms,
            hop_ms=hop_ms,
        )
        max_windows_per_pass = int(
            config_manager.get("rolling_diarization_max_windows_per_pass", 2)
        )
        max_active_runs = int(
            config_manager.get("rolling_diarization_max_active_runs", 1)
        )
        if _count_active_live_rolling_diarization_runs(session) >= max_active_runs:
            logger.info(
                "Skipping rolling diarization pass for recording %s: active run cap reached",
                recording_id,
            )
            return summary

        eligible_manifest_rows = _claim_live_rolling_diarization_manifests(
            session,
            recording_id=recording_id,
            up_to_sequence=up_to_sequence,
            config_hash=config_hash,
            max_windows_per_pass=max_windows_per_pass,
        )
        if not eligible_manifest_rows:
            return summary

        chunk_rows = _load_recording_audio_chunks(session, recording_id)
        if not chunk_rows:
            return summary

        rolling_run = ensure_processing_run(
            session,
            recording_id=recording_id,
            run_kind=ProcessingRunKind.ROLLING_DIARIZATION,
            status=ProcessingRunStatus.RUNNING,
            trigger_source="worker",
            diarization_backend="pyannote",
            config_hash=config_hash,
            span_start_ms=min(int(row.window_start_ms) for row in eligible_manifest_rows),
            span_end_ms=max(int(row.window_end_ms) for row in eligible_manifest_rows),
            idempotency_key=(
                f"rolling_diarization:{recording_id}:{config_hash}:"
                f"{int(eligible_manifest_rows[0].window_index)}:"
                f"{int(eligible_manifest_rows[-1].window_index)}"
            ),
        )
        rolling_run.status = ProcessingRunStatus.RUNNING
        rolling_run.completed_at = None
        rolling_run.error_summary = None
        session.add(rolling_run)
        for manifest_row in eligible_manifest_rows:
            manifest_row.processing_run_id = rolling_run.id
            session.add(manifest_row)
        session.commit()

        recording_speakers = session.exec(
            select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
        ).all()
        global_speakers = []
        if user_id is not None:
            global_speakers = session.exec(
                select(GlobalSpeaker).where(GlobalSpeaker.user_id == user_id)
            ).all()

        device_str = str(merged_config.get("processing_device", "auto"))
        hf_token = merged_config.get("hf_token")

        for manifest_row in eligible_manifest_rows:
            clip_path = os.path.join(
                str(live_dir),
                f"rolling_diarization_{recording_id}_{int(manifest_row.window_index)}.wav",
            )
            temp_clip_paths.append(clip_path)

            speaker_metadata_by_key: dict[str, dict[str, Any]] = {}
            speaker_embeddings_by_key: dict[str, list[float]] = {}
            diarization_result = None
            error_message = None
            with rolling_diarization_window_timer(
                recording_id=recording_id,
                window_start_s=float(manifest_row.window_start_ms) / 1000.0,
                window_end_s=float(manifest_row.window_end_ms) / 1000.0,
                window_index=int(manifest_row.window_index),
                model=get_rolling_diarization_model_name(),
                device=device_str,
                config_hash=config_hash,
                payload={
                    "chunk_start_sequence": int(manifest_row.chunk_start_sequence),
                    "chunk_end_sequence": int(manifest_row.chunk_end_sequence),
                },
                log=logger,
            ) as metric:
                if not _build_audio_window_clip(
                    manifest_row=manifest_row,
                    chunk_rows=chunk_rows,
                    clip_path=clip_path,
                ):
                    error_message = "Rolling diarization window audio could not be assembled"
                    metric["payload"]["result_available"] = False
                else:
                    diarization_result = diarize_audio(clip_path, config=merged_config)
                    metric["payload"]["result_available"] = diarization_result is not None
                    if diarization_result is None:
                        error_message = "Rolling diarization returned no result"
                    else:
                        speaker_metadata_by_key, speaker_embeddings_by_key = analyze_window_speakers(
                            diarization_result=diarization_result,
                            audio_path=clip_path,
                            device_str=device_str,
                            hf_token=hf_token,
                            recording_speakers=recording_speakers,
                            global_speakers=global_speakers,
                            window_start_ms=int(manifest_row.window_start_ms),
                            enable_embedding_matching=False,
                        )
                        metric["payload"]["speaker_count"] = len(speaker_metadata_by_key)

                window_result = persist_diarization_window_result(
                    session,
                    recording_id=recording_id,
                    manifest_row=manifest_row,
                    processing_run_id=rolling_run.id,
                    diarization_result=diarization_result,
                    config_hash=config_hash,
                    device=device_str,
                    model_name=get_rolling_diarization_model_name(),
                    error_message=error_message,
                    speaker_metadata_by_key=speaker_metadata_by_key,
                )
                if error_message is None:
                    manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_PROCESSED
                    manifest_row.diarization_processing_run_id = rolling_run.id
                    manifest_row.diarization_config_hash = config_hash
                    manifest_row.diarization_window_result_id = window_result.id
                    manifest_row.diarization_last_error = None
                    manifest_row.status = WINDOW_STATUS_LIVE_PROCESSED
                    manifest_row.last_error = None
                    tail_effective_from_ms = max(
                        int(window_result.window_start_ms),
                        int(window_result.window_end_ms) - LIVE_TAIL_RECONCILIATION_WINDOW_MS,
                    )
                    reconciliation_summary = reconcile_diarization_window_result(
                        session,
                        recording_id=recording_id,
                        window_result_id=window_result.id,
                        processing_run_id=rolling_run.id,
                        source="rolling_diarization_live_tail",
                        effective_from_ms=tail_effective_from_ms,
                        allow_speaker_reassignment=True,
                    )
                    summary["matched_turn_count"] += reconciliation_summary["matched_turn_count"]
                    summary["updated_utterance_count"] += reconciliation_summary["updated_utterance_count"]
                    summary["preserved_manual_lock_count"] += reconciliation_summary[
                        "preserved_manual_lock_count"
                    ]
                    metric["payload"]["tail_effective_from_ms"] = tail_effective_from_ms
                    metric["payload"]["reconciliation_mode"] = "speaker_reassignment_tail"
                    metric["payload"]["matched_turn_count"] = reconciliation_summary[
                        "matched_turn_count"
                    ]
                    metric["payload"]["updated_utterance_count"] = reconciliation_summary[
                        "updated_utterance_count"
                    ]
                    metric["payload"]["preserved_manual_lock_count"] = reconciliation_summary[
                        "preserved_manual_lock_count"
                    ]
                    metric["payload"]["voiceprint_update_count"] = 0
                    metric["payload"]["global_voiceprint_update_count"] = 0
                else:
                    manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_FAILED
                    manifest_row.diarization_processing_run_id = rolling_run.id
                    manifest_row.diarization_config_hash = config_hash
                    manifest_row.diarization_window_result_id = window_result.id
                    manifest_row.diarization_last_error = error_message
                    manifest_row.status = WINDOW_STATUS_FAILED
                    manifest_row.last_error = error_message
                manifest_row.processing_run_id = rolling_run.id
                session.add(manifest_row)

            summary["processed_window_count"] += 1
            session.commit()

        rolling_run.status = ProcessingRunStatus.COMPLETED
        rolling_run.completed_at = utc_now()
        rolling_run.metrics = dict(summary)
        session.add(rolling_run)
        session.commit()
        return summary
    except Exception as exc:  # noqa: BLE001
        if hasattr(session, "rollback"):
            session.rollback()
        if rolling_run is not None:
            rolling_run.status = ProcessingRunStatus.FAILED
            rolling_run.completed_at = utc_now()
            rolling_run.error_summary = str(exc).strip()[:500] or "Rolling diarization failed."
            session.add(rolling_run)
            session.commit()
        logger.warning(
            "Rolling diarization pass failed for recording %s: %s",
            recording_id,
            exc,
            exc_info=True,
        )
        return summary
    finally:
        for clip_path in temp_clip_paths:
            if os.path.exists(clip_path):
                try:
                    os.remove(clip_path)
                except OSError:
                    pass
        session.close()


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


def _get_live_speaker_display_name(index: int) -> str:
    return f"Speaker {index}"


def _get_live_speaker_label(index: int) -> str:
    return f"LIVE_{index:02d}"


def _next_live_speaker_index(existing_speakers: list[Any]) -> int:
    max_index = 0
    for speaker in existing_speakers:
        label = str(getattr(speaker, "diarization_label", "") or "")
        match = re.fullmatch(r"LIVE_(\d+)", label)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index + 1


def _get_speaker_display_name(speaker: Any) -> str:
    global_speaker = getattr(speaker, "global_speaker", None)
    return (
        getattr(speaker, "local_name", None)
        or (getattr(global_speaker, "name", None) if global_speaker else None)
        or getattr(speaker, "name", None)
        or _get_live_speaker_display_name(1)
    )


def _resolve_live_speaker(
    *,
    session,
    recording_id: int,
    user_id: int | None,
    audio_path: str,
    merged_config: dict,
    fallback_label: str | None = None,
    preferred_label: str | None = None,
    excluded_labels: list[str] | None = None,
    source_channel_evidence: LiveSourceChannelEvidence | dict[str, Any] | None = None,
) -> str:
    from sqlmodel import select

    from backend.models.speaker import RecordingSpeaker

    source_evidence = _coerce_live_source_channel_evidence(source_channel_evidence)
    if source_evidence:
        if preferred_label is None and source_evidence.preferred_label:
            preferred_label = source_evidence.preferred_label
        excluded_labels = list(excluded_labels or []) + list(source_evidence.excluded_labels)

    excluded_label_set = {str(label) for label in (excluded_labels or []) if label}
    if fallback_label in excluded_label_set:
        fallback_label = None
    if preferred_label in excluded_label_set:
        preferred_label = None

    existing_speakers = session.exec(
        select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    ).all()
    live_speakers = [
        speaker
        for speaker in existing_speakers
        if speaker.diarization_label.startswith("LIVE_")
        and speaker.diarization_label not in excluded_label_set
    ]
    live_speaker_by_label = {speaker.diarization_label: speaker for speaker in live_speakers}

    duration = 0.0
    try:
        import soundfile as sf

        info = sf.info(audio_path)
        duration = float(info.frames) / float(info.samplerate)
    except Exception:  # noqa: BLE001
        duration = 0.0

    def _record_resolution(
        label: str,
        match_kind: str,
        *,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "label": label,
            "match_kind": match_kind,
            "duration_s": round(duration, 3),
            "had_embedding": False,
            "fallback_label": fallback_label,
            "preferred_label": preferred_label,
            "excluded_labels": sorted(excluded_label_set),
            "live_speaker_count": len(live_speakers),
            "created_new_speaker": match_kind in {"new_live_speaker", "global_embedding_new"},
            "used_source_channel_authority": match_kind.startswith("preferred_source_channel"),
            "source_channel_evidence": source_evidence.to_payload() if source_evidence else None,
            "preferred_live_label_evidence": {
                "label": preferred_label,
                "available": bool(preferred_label),
                "applied": match_kind.startswith("preferred_source_channel"),
            },
            "last_stable_speaker_evidence": {
                "label": fallback_label,
                "available": bool(fallback_label),
                "applied": "fallback" in match_kind,
            },
        }
        if extra_payload:
            payload.update(extra_payload)
        record_pipeline_metric(
            stage="live_speaker_resolved",
            recording_id=recording_id,
            payload=payload,
            log=logger,
        )
        return label

    if preferred_label and preferred_label in live_speaker_by_label:
        return _record_resolution(preferred_label, "preferred_source_channel")

    if fallback_label and fallback_label in live_speaker_by_label and duration < LIVE_MIN_NEW_SPEAKER_DURATION_S:
        return _record_resolution(fallback_label, "fallback_last_label")

    if len(live_speakers) == 1:
        return _record_resolution(
            live_speakers[0].diarization_label,
            "single_live_speaker_fallback",
        )

    if live_speakers and duration < LIVE_MIN_NEW_SPEAKER_DURATION_S:
        return _record_resolution(
            live_speakers[-1].diarization_label,
            "short_pyannote_pending_latest_speaker",
        )

    next_index = _next_live_speaker_index(existing_speakers)
    live_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=_get_live_speaker_label(next_index),
        name=_get_live_speaker_display_name(next_index),
    )
    session.add(live_speaker)
    session.flush()
    return _record_resolution(
        live_speaker.diarization_label,
        "new_live_speaker",
        extra_payload={
            "speaker_assignment_strategy": "pyannote_pending_reconciliation",
        },
    )


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_live_voiceprint_learning(
    *,
    session,
    recording_id: int,
    window_result,
    speaker_embeddings_by_key: dict[str, list[float]],
) -> dict[str, int]:
    from sqlalchemy.orm.attributes import flag_modified

    from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
    from backend.processing.embedding import (
        AUTO_UPDATE_THRESHOLD,
        DRIFT_GUARD_THRESHOLD,
        cosine_similarity,
        merge_embeddings,
    )

    summary = {
        "recording_speaker_update_count": 0,
        "global_speaker_update_count": 0,
    }

    raw_payload = dict(getattr(window_result, "raw_payload", {}) or {})
    speaker_metadata_by_key = {
        str(local_speaker_key): dict(metadata or {})
        for local_speaker_key, metadata in (raw_payload.get("speaker_metadata") or {}).items()
    }

    for local_speaker_key, metadata in speaker_metadata_by_key.items():
        update_payload: dict[str, Any] = {
            "source_kind": "rolling_diarization_window",
            "window_result_id": int(window_result.id),
            "source_spans_ms": list(metadata.get("source_spans_ms") or []),
            "clean_segment_count": int(metadata.get("clean_segment_count") or 0),
            "clean_duration_ms": int(metadata.get("clean_duration_ms") or 0),
            "applied": False,
            "global_applied": False,
        }

        embedding = speaker_embeddings_by_key.get(local_speaker_key)
        if not embedding:
            update_payload["reason"] = "embedding_unavailable"
            metadata["voiceprint_update"] = update_payload
            continue

        if update_payload["clean_duration_ms"] < int(LIVE_VOICEPRINT_MIN_CLEAN_DURATION_S * 1000.0):
            update_payload["reason"] = "insufficient_clean_duration"
            metadata["voiceprint_update"] = update_payload
            continue

        matched_recording_speaker_id = metadata.get("matched_recording_speaker_id")
        try:
            matched_recording_speaker_id = int(matched_recording_speaker_id)
        except (TypeError, ValueError):
            matched_recording_speaker_id = None

        if matched_recording_speaker_id is None:
            update_payload["reason"] = "no_matched_recording_speaker"
            metadata["voiceprint_update"] = update_payload
            continue

        recording_speaker = session.get(RecordingSpeaker, matched_recording_speaker_id)
        if recording_speaker is None or getattr(recording_speaker, "merged_into_id", None):
            update_payload["reason"] = "recording_speaker_unavailable"
            metadata["voiceprint_update"] = update_payload
            continue

        confidence_candidates = [
            _coerce_float(metadata.get("match_confidence")),
            _coerce_float(metadata.get("best_recording_speaker_score")),
            _coerce_float(metadata.get("best_global_speaker_score")),
        ]
        duration_confidence = min(
            update_payload["clean_duration_ms"] / 5000.0,
            1.0,
        )
        confidence_candidates.append(duration_confidence)
        confidence = max(
            candidate for candidate in confidence_candidates if candidate is not None
        )
        update_payload["confidence"] = round(float(confidence), 4)
        update_payload["target_recording_speaker_id"] = int(recording_speaker.id)

        recording_similarity = None
        if getattr(recording_speaker, "embedding", None):
            recording_similarity = cosine_similarity(recording_speaker.embedding, embedding)
            update_payload["recording_similarity"] = round(float(recording_similarity), 4)
            if recording_similarity < DRIFT_GUARD_THRESHOLD:
                update_payload["reason"] = "recording_drift_guard_rejected"
                metadata["voiceprint_update"] = update_payload
                continue
            recording_speaker.embedding = merge_embeddings(
                recording_speaker.embedding,
                embedding,
                alpha=LIVE_RECORDING_SPEAKER_VOICEPRINT_ALPHA,
                drift_guard=True,
            )
        else:
            recording_speaker.embedding = list(embedding)

        existing_identity_confidence = _coerce_float(
            getattr(recording_speaker, "identity_confidence", None)
        ) or 0.0
        recording_speaker.identity_confidence = max(existing_identity_confidence, confidence)
        session.add(recording_speaker)
        summary["recording_speaker_update_count"] += 1
        update_payload["applied"] = True

        global_speaker_id = getattr(recording_speaker, "global_speaker_id", None)
        if global_speaker_id is not None:
            global_speaker = session.get(GlobalSpeaker, int(global_speaker_id))
            if global_speaker is None:
                update_payload["global_reason"] = "global_speaker_unavailable"
            elif getattr(global_speaker, "is_voiceprint_locked", False):
                update_payload["global_reason"] = "global_voiceprint_locked"
            else:
                global_similarity = None
                if getattr(global_speaker, "embedding", None):
                    global_similarity = cosine_similarity(global_speaker.embedding, embedding)
                    update_payload["global_similarity"] = round(float(global_similarity), 4)
                    if global_similarity < DRIFT_GUARD_THRESHOLD:
                        update_payload["global_reason"] = "global_drift_guard_rejected"
                    else:
                        best_global_speaker_id = metadata.get("best_global_speaker_id")
                        best_global_score = _coerce_float(metadata.get("best_global_speaker_score"))
                        if (
                            best_global_speaker_id is not None
                            and int(best_global_speaker_id) == int(global_speaker.id)
                            and best_global_score is not None
                            and best_global_score < AUTO_UPDATE_THRESHOLD
                        ):
                            update_payload["global_reason"] = "below_auto_update_threshold"
                        else:
                            global_speaker.embedding = merge_embeddings(
                                global_speaker.embedding,
                                embedding,
                                alpha=LIVE_GLOBAL_SPEAKER_VOICEPRINT_ALPHA,
                                drift_guard=True,
                            )
                            session.add(global_speaker)
                            summary["global_speaker_update_count"] += 1
                            update_payload["global_applied"] = True
                else:
                    global_speaker.embedding = list(embedding)
                    session.add(global_speaker)
                    summary["global_speaker_update_count"] += 1
                    update_payload["global_applied"] = True

        metadata["voiceprint_update"] = update_payload

        record_pipeline_metric(
            stage="live_voiceprint_learned",
            recording_id=recording_id,
            payload={
                "window_result_id": int(window_result.id),
                "local_speaker_key": local_speaker_key,
                **update_payload,
            },
            log=logger,
        )

    raw_payload["speaker_metadata"] = speaker_metadata_by_key
    window_result.raw_payload = raw_payload
    try:
        flag_modified(window_result, "raw_payload")
    except Exception:  # noqa: BLE001
        pass
    session.add(window_result)

    return summary


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

    # The live lane is only meaningful while the recording is uploading. Once
    # finalize() changes the recording status, queued live work must stop even
    # though uploaded chunk files may remain on disk until lifecycle cleanup.
    # The final pipeline is authoritative after the status flip.
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if not recording or recording.status != RecordingStatus.UPLOADING:
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
            region_path = str(live_dir / f"speaker_region_{sp['start']:.3f}_{sp['end']:.3f}.wav")
            try:
                import silero_vad

                tensor = clip if clip.ndim > 1 else clip.unsqueeze(0)
                silero_vad.save_audio(clip_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
                region_tensor = region if region.ndim > 1 else region.unsqueeze(0)
                silero_vad.save_audio(
                    region_path,
                    region_tensor,
                    sampling_rate=LIVE_SAMPLE_RATE,
                )
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
                preferred_label = source_channel_evidence.preferred_label
                excluded_labels = list(source_channel_evidence.excluded_labels)
                session = get_sync_session()
                try:
                    speaker_label = _resolve_live_speaker(
                        session=session,
                        recording_id=recording_id,
                        user_id=user_id,
                        audio_path=region_path,
                        merged_config=merged_config,
                        fallback_label=state.get("last_speaker_label"),
                        preferred_label=preferred_label,
                        excluded_labels=excluded_labels,
                        source_channel_evidence=source_channel_evidence,
                    )
                    session.commit()
                except Exception as speaker_exc:  # noqa: BLE001
                    if hasattr(session, "rollback"):
                        session.rollback()
                    logger.warning(
                        "Live speaker matching failed for recording %s region %.2f-%.2f: %s",
                        recording_id,
                        combined_abs_start + sp["start"],
                        combined_abs_start + sp["end"],
                        speaker_exc,
                        exc_info=True,
                    )
                finally:
                    session.close()

                record_pipeline_metric(
                    stage="live_source_channel_authority",
                    recording_id=recording_id,
                    payload={
                        "sequence": sequence,
                        "run": list(run),
                        "region_start_ms": region_start_ms,
                        "region_end_ms": region_end_ms,
                        **source_channel_evidence.to_payload(),
                        "resolved_label": speaker_label,
                        "known_source_channel_labels": dict(source_channel_labels),
                    },
                    log=logger,
                )

            finally:
                if os.path.exists(clip_path):
                    try:
                        os.remove(clip_path)
                    except OSError:
                        pass
                if os.path.exists(region_path):
                    try:
                        os.remove(region_path)
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
            if recording and recording.status == RecordingStatus.UPLOADING:
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

            if 'run' in locals() and run and 'merged_config' in locals():
                _run_live_rolling_diarization_pass(
                    recording_id=recording_id,
                    up_to_sequence=run[-1],
                    user_id=user_id,
                    merged_config=merged_config,
                    live_dir=live_dir,
                )

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
