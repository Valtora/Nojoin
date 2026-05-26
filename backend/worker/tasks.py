import os
import shutil
import logging
import hashlib
import time
from datetime import datetime, timedelta
import warnings
import urllib.error
import requests.exceptions

from typing import TYPE_CHECKING, Iterable, Sequence
from celery import Task
from celery.signals import worker_ready
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.pipeline import (
    DiarizationWindowResult,
    DiarizationWindowTurn,
    ProcessingRun,
    ProcessingRunKind,
    ProcessingRunStatus,
    RecordingAudioChunk,
    RecordingAudioWindowManifest,
    TranscriptUtteranceState,
)
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.tag import RecordingTag
from backend.models.user import User
from backend.models.invitation import Invitation
from backend.models.chat import ChatMessage
from backend.core.exceptions import AudioProcessingError, AudioFormatError, VADNoSpeechError
from backend.processing.pipeline_metrics import pipeline_metric_timer, record_pipeline_metric
from backend.services.calendar_link_service import auto_link_recording

# Heavy processing imports moved inside tasks to avoid loading torch in API
from backend.models.document import Document, DocumentStatus
from backend.models.context_chunk import ContextChunk
from backend.utils.config_manager import (
    MEETING_EDGE_CONTEXT_LEVEL_MAX,
    config_manager,
    get_meeting_edge_context_level,
    is_meeting_edge_enabled,
)
from backend.utils.llm_config import (
    LLM_PURPOSE_MEETING_EDGE,
    ResolvedLLMConfig,
    resolve_llm_config,
)
from backend.utils.meeting_edge import (
    MeetingEdgeRequest,
    merge_meeting_edge_concept_history,
    serialize_meeting_edge_result,
)
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
    get_speakers_eligible_for_llm_renaming,
)
from backend.utils.meeting_notes import (
    MeetingEventContext,
    build_recording_speaker_map,
    format_segments_for_llm,
    meeting_event_context_from_calendar_event,
)
from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceResult,
    build_mapping_based_speaker_suggestions,
    build_persisted_speaker_suggestion,
    detect_rule_based_speaker_suggestions,
    persist_transcript_speaker_suggestions,
    supersede_pending_transcript_speaker_suggestions,
)
from backend.models.calendar import CalendarEvent
from backend.utils.audio_windows import (
    WINDOW_DIARIZATION_STATUS_FAILED,
    WINDOW_DIARIZATION_STATUS_PROCESSED,
    WINDOW_STATUS_FAILED,
    WINDOW_STATUS_CATCH_UP_PROCESSED,
    collect_pending_chunk_spans,
    count_manifest_statuses,
    mark_audio_windows_processed,
    window_asr_is_processed,
    window_diarization_is_processed,
)
from backend.utils.recording_storage import (
    cleanup_recording_audio_chunks,
    cleanup_stale_recording_artifacts,
    mark_recording_audio_chunks_ready_for_cleanup,
)
from backend.utils.status_manager import update_recording_status
from backend.utils.time import utc_now
from backend.utils.asr_window_results import (
    complete_recording_asr_window_result,
    fail_recording_asr_window_result,
    get_recording_asr_window_result,
    get_reusable_catch_up_segments,
    start_recording_asr_window_result,
)
from backend.utils.canonical_pipeline import (
    ROLLING_DIARIZATION_CONFIDENCE_FLOOR,
    ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL,
    build_transcript_segments_for_read,
    build_reusable_live_segments,
    ensure_processing_run,
    finalize_utterances_from_segments,
    reconcile_completed_diarization_windows,
    refine_recording_utterances_via_segmentation,
)
from backend.utils.rolling_diarization import (
    build_diarization_window_payload,
    build_rolling_diarization_config_hash,
    get_rolling_diarization_model_name,
    persist_diarization_window_result,
)
from backend.processing.text_embedding import get_text_embedding_service

if TYPE_CHECKING:
    from backend.processing.embedding import cosine_similarity, merge_embeddings
    from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
    from backend.utils.audio import get_audio_duration, convert_to_mp3, convert_to_proxy_mp3
    from backend.processing.llm_services import get_llm_backend
    import torch

logger = logging.getLogger(__name__)

FINAL_DIARIZATION_SPAN_PADDING_MS = 1000
FINAL_DIARIZATION_BRIDGE_GAP_MS = 1500

# Suppress specific warnings in the worker process
warnings.filterwarnings("ignore", message=r".*std\(\): degrees of freedom is <= 0.*")

AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS = 300
AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS = 97
AUTOMATIC_MEETING_INTELLIGENCE_STAGE = "Generating Notes"
AUTOMATIC_MEETING_INTELLIGENCE_STEP = "Generating meeting notes..."

MEETING_EDGE_TIMEOUT_SECONDS = 90
MEETING_EDGE_MIN_SEGMENTS = 3
MEETING_EDGE_MIN_WORDS = 80
MEETING_EDGE_FOCUSED_MIN_SEGMENTS = 2
MEETING_EDGE_FOCUSED_MIN_WORDS = 35
MEETING_EDGE_MIN_REFRESH_SECONDS = 20
MEETING_EDGE_MIN_NEW_SEGMENTS = 3
MEETING_EDGE_MIN_NEW_WORDS = 60
MEETING_EDGE_RECENT_SEGMENTS = 12
MEETING_EDGE_MAX_TRANSCRIPT_CHARS = 6000
MEETING_EDGE_STATUS_IDLE = "idle"
MEETING_EDGE_STATUS_UPDATING = "updating"
MEETING_EDGE_STATUS_READY = "ready"
MEETING_EDGE_STATUS_ERROR = "error"


def _final_asr_config_hash(merged_config: dict) -> str:
    return hashlib.sha256(
        "|".join(
            [
                str(merged_config.get("transcription_backend", "whisper")),
                str(merged_config.get("whisper_model_size", "turbo")),
                str(merged_config.get("parakeet_model", "parakeet-tdt-0.6b-v3")),
                str(merged_config.get("canary_model", "nemo-canary-1b-v2")),
                str(merged_config.get("processing_device", "auto")),
                str(bool(merged_config.get("use_gpu", True))),
            ]
        ).encode("utf-8")
    ).hexdigest()


def _paths_point_to_same_media(path_a: str | None, path_b: str | None) -> bool:
    if not path_a or not path_b:
        return False

    try:
        if os.path.exists(path_a) and os.path.exists(path_b):
            return os.path.samefile(path_a, path_b)
    except OSError:
        pass

    return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))


def _can_delete_source_audio(recording: Recording) -> bool:
    if not recording.audio_path or not recording.proxy_path:
        return False
    if not os.path.exists(recording.audio_path) or not os.path.exists(recording.proxy_path):
        return False

    return not _paths_point_to_same_media(recording.audio_path, recording.proxy_path)


def _format_notes_generation_error(error: Exception | str) -> str:
    message = str(error).strip() or "Meeting notes could not be generated."
    if len(message) > 500:
        message = f"{message[:497]}..."
    return message


def _mark_notes_generation_error(
    session,
    recording: Recording | None,
    transcript: Transcript | None,
    error: Exception | str,
) -> None:
    if not transcript:
        return

    transcript.notes_status = "error"
    transcript.error_message = _format_notes_generation_error(error)
    session.add(transcript)

    if recording:
        recording.processing_step = "Error generating notes"
        session.add(recording)

    session.commit()

    if recording:
        update_recording_status(session, recording.id)


def _complete_speaker_inference_task(
    session,
    recording: Recording | None,
) -> None:
    if not recording:
        return

    recording.status = RecordingStatus.PROCESSED
    recording.client_status = ClientStatus.IDLE
    recording.processing_step = "Completed"
    session.add(recording)
    session.commit()


def _build_exact_global_speaker_name_map(
    session,
    *,
    user_id: int | None,
    suggested_names: Sequence[str],
) -> dict[str, int]:
    cleaned_names = sorted({name.strip() for name in suggested_names if str(name).strip()})
    if not user_id or not cleaned_names:
        return {}

    bind = session.get_bind()
    if bind is not None and not inspect(bind).has_table("global_speakers"):
        logger.debug(
            "Skipping exact global speaker matching for recording suggestions because the global_speakers table is unavailable.",
        )
        return {}

    global_speakers = session.exec(
        select(GlobalSpeaker)
        .where(GlobalSpeaker.user_id == user_id)
        .where(GlobalSpeaker.name.in_(cleaned_names))
    ).all()
    return {
        str(speaker.name).strip(): int(speaker.id)
        for speaker in global_speakers
        if speaker.id is not None and speaker.name
    }


def _build_persisted_speaker_name_suggestions(
    session,
    *,
    recording: Recording,
    speakers: Sequence[RecordingSpeaker],
    inference_result: SpeakerInferenceResult,
    origin: str,
    provider: str | None,
) -> list[dict[str, object]]:
    speakers_by_label = {speaker.diarization_label: speaker for speaker in speakers}
    exact_global_name_map = _build_exact_global_speaker_name_map(
        session,
        user_id=recording.user_id,
        suggested_names=[
            suggestion.suggested_name for suggestion in inference_result.suggestions
        ],
    )

    persisted: list[dict[str, object]] = []
    for suggestion in inference_result.suggestions:
        speaker = speakers_by_label.get(suggestion.diarization_label)
        if speaker is None:
            continue
        if speaker.merged_into_id or speaker.local_name or speaker.global_speaker_id:
            logger.info(
                "Skipping speaker suggestion for trusted or merged label %s",
                suggestion.diarization_label,
            )
            continue

        persisted.append(
            build_persisted_speaker_suggestion(
                suggestion,
                origin=origin,
                provider=provider,
                recording_speaker_id=speaker.id,
                suggested_global_speaker_id=exact_global_name_map.get(
                    suggestion.suggested_name
                ),
            )
        )

    return persisted


def _persist_generated_speaker_name_suggestions(
    session,
    *,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    inference_result: SpeakerInferenceResult,
    origin: str,
    provider: str | None,
    replaced_reason: str,
) -> int:
    if not inference_result.suggestions:
        return 0

    persisted = _build_persisted_speaker_name_suggestions(
        session,
        recording=recording,
        speakers=speakers,
        inference_result=inference_result,
        origin=origin,
        provider=provider,
    )
    if not persisted:
        return 0

    persist_transcript_speaker_suggestions(
        transcript,
        persisted,
        replaced_reason=replaced_reason,
    )
    flag_modified(transcript, "speaker_name_suggestions")
    session.add(transcript)
    return len(persisted)


def _supersede_pending_speaker_name_suggestions_for_labels(
    session,
    *,
    transcript: Transcript,
    diarization_labels: Iterable[str],
    reason: str,
) -> int:
    superseded = supersede_pending_transcript_speaker_suggestions(
        transcript,
        diarization_labels=diarization_labels,
        reason=reason,
    )
    if not superseded:
        return 0
    flag_modified(transcript, "speaker_name_suggestions")
    session.add(transcript)
    return len(superseded)


def _llm_backend_from_config(llm_config: ResolvedLLMConfig):
    from backend.processing.llm_services import get_llm_backend

    return get_llm_backend(
        llm_config.provider,
        api_key=llm_config.api_key,
        model=llm_config.model,
        api_url=llm_config.api_url,
    )


def _count_meeting_edge_words(segments: Sequence[dict]) -> int:
    total = 0
    for segment in segments:
        total += len(str(segment.get("text", "")).split())
    return total


def _has_meeting_edge_signal(
    *,
    segment_count: int,
    word_count: int,
    focus_text: str | None,
) -> bool:
    min_segments = (
        MEETING_EDGE_FOCUSED_MIN_SEGMENTS if focus_text else MEETING_EDGE_MIN_SEGMENTS
    )
    min_words = MEETING_EDGE_FOCUSED_MIN_WORDS if focus_text else MEETING_EDGE_MIN_WORDS
    return word_count >= min_words or (
        segment_count >= min_segments and word_count >= max(18, min_words // 2)
    )


def _build_recent_meeting_edge_transcript(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
) -> str:
    lines: list[str] = []
    total_chars = 0

    for segment in reversed(list(segments)[-MEETING_EDGE_RECENT_SEGMENTS:]):
        rendered = format_segments_for_llm([segment], speaker_map)
        if not rendered:
            continue
        rendered_length = len(rendered) + 1
        if lines and total_chars + rendered_length > MEETING_EDGE_MAX_TRANSCRIPT_CHARS:
            break
        lines.append(rendered)
        total_chars += rendered_length

    return "\n".join(reversed(lines)).strip()


def _hash_meeting_edge_text(value: str | None) -> str:
    cleaned = (value or "").strip()
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def _build_meeting_edge_source_signature(
    *,
    recent_transcript: str,
    focus_text: str | None,
    user_notes: str | None,
    config_signature: str,
) -> str:
    payload = "\n||\n".join(
        [recent_transcript.strip(), (focus_text or "").strip(), (user_notes or "").strip(), config_signature]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _parse_meeting_edge_generated_at(payload: dict | None) -> datetime | None:
    if not isinstance(payload, dict):
        return None

    raw_value = payload.get("generated_at")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None

    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _should_refresh_meeting_edge(
    *,
    transcript: Transcript,
    source_signature: str,
    current_segment_count: int,
    current_word_count: int,
    focus_text: str | None,
    user_notes: str | None,
) -> bool:
    if transcript.meeting_edge_source_signature == source_signature and transcript.meeting_edge_status in {
        MEETING_EDGE_STATUS_READY,
        MEETING_EDGE_STATUS_UPDATING,
        MEETING_EDGE_STATUS_ERROR,
    }:
        return False

    previous_payload = (
        transcript.meeting_edge_payload if isinstance(transcript.meeting_edge_payload, dict) else {}
    )
    previous_generated_at = _parse_meeting_edge_generated_at(previous_payload)
    previous_segment_count = int(previous_payload.get("source_segment_count") or 0)
    previous_word_count = int(previous_payload.get("source_word_count") or 0)
    focus_changed = previous_payload.get("focus_hash") != _hash_meeting_edge_text(focus_text)
    user_notes_changed = previous_payload.get("user_notes_hash") != _hash_meeting_edge_text(user_notes)

    if focus_changed or user_notes_changed or not previous_generated_at:
        return True

    elapsed_seconds = max((utc_now() - previous_generated_at).total_seconds(), 0.0)
    new_segment_count = max(current_segment_count - previous_segment_count, 0)
    new_word_count = max(current_word_count - previous_word_count, 0)

    if elapsed_seconds < MEETING_EDGE_MIN_REFRESH_SECONDS:
        return False

    return (
        new_segment_count >= MEETING_EDGE_MIN_NEW_SEGMENTS
        or new_word_count >= MEETING_EDGE_MIN_NEW_WORDS
    )


def _format_recording_timestamp(seconds: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(max(float(seconds), 0.0)))


def _load_recording_audio_chunks(session, recording_id: int) -> list[RecordingAudioChunk]:
    return session.exec(
        select(RecordingAudioChunk)
        .where(RecordingAudioChunk.recording_id == recording_id)
        .order_by(RecordingAudioChunk.sequence_no)
    ).all()


def _load_recording_audio_window_manifests(
    session,
    recording_id: int,
) -> list[RecordingAudioWindowManifest]:
    return session.exec(
        select(RecordingAudioWindowManifest)
        .where(RecordingAudioWindowManifest.recording_id == recording_id)
        .order_by(RecordingAudioWindowManifest.window_index)
    ).all()


def _to_optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _segment_requires_final_diarization_check(segment: dict) -> bool:
    speaker_label = str(segment.get("speaker") or "").strip().upper()
    speaker_state = str(segment.get("speaker_state") or "").strip().lower()
    speaker_confidence = _to_optional_float(segment.get("speaker_confidence"))

    if segment.get("provisional") is True:
        return True
    if speaker_label == "UNKNOWN":
        return True
    if speaker_state == ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL:
        return True
    if speaker_state == "" and str(segment.get("segment_source") or "") in {"live", "catch_up"}:
        return True
    if speaker_confidence is not None and speaker_confidence < ROLLING_DIARIZATION_CONFIDENCE_FLOOR:
        return True
    if list(segment.get("overlapping_speakers") or []):
        return True
    return False


def _is_unresolved_speaker_label(label: object) -> bool:
    return str(label or "").strip().upper() in {"", "UNKNOWN"}


def _collect_ordered_final_speaker_labels(final_segments: Sequence[dict]) -> list[str]:
    ordered_speakers: list[str] = []
    seen_speakers: set[str] = set()
    for seg in final_segments:
        speaker_label = str(seg.get("speaker") or "UNKNOWN")
        if not _is_unresolved_speaker_label(speaker_label) and speaker_label not in seen_speakers:
            ordered_speakers.append(speaker_label)
            seen_speakers.add(speaker_label)
        for overlapping_spk in seg.get("overlapping_speakers", []):
            overlapping_label = str(overlapping_spk or "UNKNOWN")
            if _is_unresolved_speaker_label(overlapping_label) or overlapping_label in seen_speakers:
                continue
            ordered_speakers.append(overlapping_label)
            seen_speakers.add(overlapping_label)
    return ordered_speakers


def _collect_low_confidence_diarization_spans(
    live_segments_for_reuse: Sequence[dict],
) -> list[dict[str, int]]:
    spans: list[dict[str, int]] = []
    for segment in live_segments_for_reuse:
        if not _segment_requires_final_diarization_check(segment):
            continue

        start_ms = max(
            0,
            int(round(float(segment.get("start", 0.0)) * 1000.0)) - FINAL_DIARIZATION_SPAN_PADDING_MS,
        )
        end_ms = max(
            start_ms,
            int(round(float(segment.get("end", 0.0)) * 1000.0)) + FINAL_DIARIZATION_SPAN_PADDING_MS,
        )

        if spans and start_ms <= (int(spans[-1]["end_ms"]) + FINAL_DIARIZATION_BRIDGE_GAP_MS):
            spans[-1]["end_ms"] = max(int(spans[-1]["end_ms"]), end_ms)
            spans[-1]["segment_count"] = int(spans[-1].get("segment_count", 0)) + 1
            continue

        spans.append(
            {
                "start_ms": int(start_ms),
                "end_ms": int(end_ms),
                "segment_count": 1,
            }
        )
    return spans


def _build_final_diarization_plan(
    *,
    live_segments_for_reuse: Sequence[dict],
    reused_live_transcript_segments: Sequence[dict],
    engine_override: dict | None,
    completed_window_replay_available: bool = False,
) -> dict[str, object]:
    if engine_override:
        return {
            "should_run": True,
            "reason": "engine_override",
            "low_confidence_spans": [],
        }

    if not reused_live_transcript_segments or not live_segments_for_reuse:
        return {
            "should_run": True,
            "reason": "no_live_reuse",
            "low_confidence_spans": [],
        }

    low_confidence_spans = _collect_low_confidence_diarization_spans(live_segments_for_reuse)
    if low_confidence_spans:
        return {
            "should_run": True,
            "reason": "low_confidence_spans",
            "low_confidence_spans": low_confidence_spans,
            "completed_window_replay_available": bool(completed_window_replay_available),
        }

    return {
        "should_run": False,
        "reason": "confident_live_reuse",
        "low_confidence_spans": [],
        "completed_window_replay_available": bool(completed_window_replay_available),
    }


def _build_catch_up_segments(
    *,
    session,
    recording: Recording,
    processed_audio_path: str,
    merged_config: dict,
    transcribe_audio,
    extract_audio_clip,
    temp_files: list[str],
    log: logging.Logger,
) -> tuple[list[dict], set[int], ProcessingRun | None]:
    manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
    chunk_rows = _load_recording_audio_chunks(session, recording.id)
    raw_pending_spans = collect_pending_chunk_spans(manifest_rows, chunk_rows)
    pending_manifest_rows = [
        row
        for row in manifest_rows
        if row.id is not None
        and not window_asr_is_processed(row)
    ]
    pending_window_ids = {
        int(row.id)
        for row in pending_manifest_rows
    }
    if not raw_pending_spans and not pending_window_ids:
        return [], set(), None

    span_start_ms = min(
        [int(row.window_start_ms) for row in pending_manifest_rows]
        or [span.start_ms for span in raw_pending_spans],
        default=0,
    )
    span_end_ms = max(
        [int(row.window_end_ms) for row in pending_manifest_rows]
        or [span.end_ms for span in raw_pending_spans],
        default=0,
    )
    catch_up_idempotency_parts = (
        ",".join(f"{span.start_sequence}-{span.end_sequence}" for span in raw_pending_spans)
        if raw_pending_spans
        else f"windows:{','.join(str(window_id) for window_id in sorted(pending_window_ids))}"
    )
    catch_up_run = ensure_processing_run(
        session,
        recording_id=recording.id,
        run_kind=ProcessingRunKind.CATCH_UP,
        status=ProcessingRunStatus.RUNNING,
        trigger_source="worker",
        transcription_backend=merged_config.get("transcription_backend"),
        span_start_ms=span_start_ms,
        span_end_ms=span_end_ms,
        idempotency_key=(
            "catch_up:"
            f"{recording.id}:"
            f"{_final_asr_config_hash(merged_config)}:"
            f"{catch_up_idempotency_parts}"
        ),
    )
    catch_up_run.status = ProcessingRunStatus.RUNNING
    catch_up_run.completed_at = None
    catch_up_run.error_summary = None
    session.add(catch_up_run)

    catch_up_segments: list[dict] = []
    status_counts = count_manifest_statuses(manifest_rows)
    ledger_enabled = bool(config_manager.get("enable_asr_window_result_ledger", True))
    pending_spans: list = []
    reused_span_count = 0
    reused_segment_count = 0
    legacy_payload_gap_count = 0

    for span in raw_pending_spans:
        existing_result = None
        reusable_segments = None
        if ledger_enabled:
            existing_result = get_recording_asr_window_result(
                session,
                recording_id=recording.id,
                source_kind="catch_up",
                span_start_ms=span.start_ms,
                span_end_ms=span.end_ms,
                chunk_start_sequence=span.start_sequence,
                chunk_end_sequence=span.end_sequence,
                config=merged_config,
                config_hash=_final_asr_config_hash(merged_config),
            )
            reusable_segments = get_reusable_catch_up_segments(existing_result)

        if reusable_segments is not None:
            reused_span_count += 1
            reused_segment_count += len(reusable_segments)
            catch_up_segments.extend(reusable_segments)
            continue

        if ledger_enabled and existing_result is not None:
            status_value = getattr(existing_result.status, "value", existing_result.status)
            if status_value == "completed":
                legacy_payload_gap_count += 1

        pending_spans.append(span)

    record_pipeline_metric(
        stage="catch_up_detected",
        recording_id=recording.id,
        payload={
            "pending_window_count": len(pending_window_ids),
            "pending_span_count": len(raw_pending_spans),
            "rerun_span_count": len(pending_spans),
            "reused_span_count": reused_span_count,
            "reused_segment_count": reused_segment_count,
            "legacy_payload_gap_count": legacy_payload_gap_count,
            "window_status_counts": status_counts,
        },
        log=log,
    )

    for span in pending_spans:
        clip_path = os.path.join(
            os.path.dirname(processed_audio_path),
            f"catch_up_{recording.id}_{span.start_sequence}_{span.end_sequence}.wav",
        )
        extract_audio_clip(
            processed_audio_path,
            clip_path,
            start_seconds=span.start_ms / 1000.0,
            end_seconds=span.end_ms / 1000.0,
        )
        temp_files.append(clip_path)

        with pipeline_metric_timer(
            stage="catch_up_asr_span",
            recording_id=recording.id,
            payload={
                "start_sequence": span.start_sequence,
                "end_sequence": span.end_sequence,
                "span_start_ms": span.start_ms,
                "span_end_ms": span.end_ms,
                "engine": merged_config.get("transcription_backend"),
            },
            log=log,
        ) as metric:
            if ledger_enabled:
                start_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                )
            try:
                result = transcribe_audio(clip_path, config=merged_config)
            except Exception as exc:
                if ledger_enabled:
                    fail_recording_asr_window_result(
                        session,
                        recording_id=recording.id,
                        processing_run_id=catch_up_run.id if catch_up_run else None,
                        source_kind="catch_up",
                        span_start_ms=span.start_ms,
                        span_end_ms=span.end_ms,
                        chunk_start_sequence=span.start_sequence,
                        chunk_end_sequence=span.end_sequence,
                        config=merged_config,
                        config_hash=_final_asr_config_hash(merged_config),
                        error_summary=str(exc).strip()[:500] or "Catch-up ASR invocation failed.",
                        error_payload={"error_type": exc.__class__.__name__},
                    )
                raise
            metric["payload"]["segment_count"] = len((result or {}).get("segments", []))

        result_segments: list[dict] = []
        for segment in (result or {}).get("segments", []):
            text = str(segment.get("text", "")).strip()
            if not text:
                continue

            relative_start = float(segment.get("start", 0.0) or 0.0)
            relative_end = float(segment.get("end", 0.0) or 0.0)
            if relative_end <= relative_start:
                continue

            result_segments.append(
                {
                    "start": relative_start,
                    "end": relative_end,
                    "speaker": str(segment.get("speaker") or "UNKNOWN"),
                    "text": text,
                    "segment_source": "catch_up",
                }
            )
            catch_up_segments.append(
                {
                    "start": span.start_ms / 1000.0 + relative_start,
                    "end": span.start_ms / 1000.0 + relative_end,
                    "speaker": str(segment.get("speaker") or "UNKNOWN"),
                    "text": text,
                    "segment_source": "catch_up",
                }
            )

        if ledger_enabled:
            if result is None:
                fail_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    error_summary="Catch-up ASR returned no result.",
                    error_payload={"error_type": "empty_result"},
                )
            else:
                complete_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    result_payload={
                        "segment_count": len(result_segments),
                        "text_chars": len((result or {}).get("text") or ""),
                        "segments": result_segments,
                    },
                )

    catch_up_segments.sort(
        key=lambda segment: (
            float(segment.get("start", 0.0)),
            float(segment.get("end", 0.0)),
            str(segment.get("text", "")),
        )
    )

    return catch_up_segments, pending_window_ids, catch_up_run


def _recording_has_completed_diarization_windows(
    session,
    *,
    recording_id: int,
    effective_from_ms: int = 0,
) -> bool:
    return session.exec(
        select(DiarizationWindowResult)
        .where(DiarizationWindowResult.recording_id == recording_id)
        .where(DiarizationWindowResult.status == "completed")
        .where(DiarizationWindowResult.window_end_ms > int(effective_from_ms))
        .limit(1)
    ).first() is not None


def _build_diarization_window_payload(
    diarization_result,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    turn_payloads: list[dict[str, object]] = []
    speaker_labels: set[str] = set()

    if diarization_result is not None and hasattr(diarization_result, "itertracks"):
        for segment, track, label in diarization_result.itertracks(yield_label=True):
            start_ms = window_start_ms + int(round(float(segment.start) * 1000.0))
            end_ms = window_start_ms + int(round(float(segment.end) * 1000.0))
            if end_ms <= start_ms:
                continue
            label_value = str(label)
            speaker_labels.add(label_value)
            turn_payloads.append(
                {
                    "local_speaker_key": label_value,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "track": str(track),
                }
            )

    turn_payloads.sort(
        key=lambda payload: (
            int(payload["start_ms"]),
            int(payload["end_ms"]),
            str(payload["local_speaker_key"]),
        )
    )
    return (
        {
            "window_start_ms": int(window_start_ms),
            "window_end_ms": int(window_end_ms),
            "speaker_labels": sorted(speaker_labels),
            "turn_count": len(turn_payloads),
            "turns": turn_payloads,
        },
        turn_payloads,
    )


def _catch_up_diarization_config_hash(merged_config: dict) -> str:
    return build_rolling_diarization_config_hash(
        merged_config,
        target_window_ms=int(merged_config.get("rolling_diarization_window_ms", 20_000)),
        hop_ms=int(merged_config.get("rolling_diarization_hop_ms", 5_000)),
    )


def _persist_catch_up_diarization_window(
    session,
    *,
    recording_id: int,
    manifest_row: RecordingAudioWindowManifest,
    processing_run_id: int | None,
    diarization_result,
    merged_config: dict,
    device: str,
    error_message: str | None = None,
) -> DiarizationWindowResult:
    return persist_diarization_window_result(
        session,
        recording_id=recording_id,
        manifest_row=manifest_row,
        processing_run_id=processing_run_id,
        diarization_result=diarization_result,
        config_hash=_catch_up_diarization_config_hash(merged_config),
        device=device,
        model_name=get_rolling_diarization_model_name(),
        error_message=error_message,
    )


def _run_catch_up_diarization_windows(
    *,
    session,
    recording: Recording,
    processed_audio_path: str,
    merged_config: dict,
    diarize_audio,
    extract_audio_clip,
    processing_run_id: int | None,
    temp_files: list[str],
    log: logging.Logger,
) -> tuple[set[int], set[int]]:
    manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
    config_hash = _catch_up_diarization_config_hash(merged_config)
    completed_window_indexes = {
        int(window_index)
        for window_index in session.exec(
            select(DiarizationWindowResult.window_index)
            .where(DiarizationWindowResult.recording_id == recording.id)
            .where(DiarizationWindowResult.config_hash == config_hash)
            .where(DiarizationWindowResult.status == "completed")
        ).all()
    }
    pending_manifest_rows = [
        row
        for row in manifest_rows
        if row.id is not None
        and window_asr_is_processed(row)
        and int(row.window_index) not in completed_window_indexes
        and not window_diarization_is_processed(
            row,
            config_hash=config_hash,
        )
    ]
    if not pending_manifest_rows:
        return set(), set()

    completed_window_ids: set[int] = set()
    failed_window_ids: set[int] = set()
    device = str(merged_config.get("processing_device", "auto"))

    for manifest_row in pending_manifest_rows:
        clip_path = os.path.join(
            os.path.dirname(processed_audio_path),
            f"catch_up_diarize_{recording.id}_{manifest_row.window_index}.wav",
        )
        extract_audio_clip(
            processed_audio_path,
            clip_path,
            start_seconds=float(manifest_row.window_start_ms) / 1000.0,
            end_seconds=float(manifest_row.window_end_ms) / 1000.0,
        )
        temp_files.append(clip_path)

        with pipeline_metric_timer(
            stage="catch_up_diarization_window",
            recording_id=recording.id,
            payload={
                "window_index": int(manifest_row.window_index),
                "window_start_ms": int(manifest_row.window_start_ms),
                "window_end_ms": int(manifest_row.window_end_ms),
                "chunk_start_sequence": int(manifest_row.chunk_start_sequence),
                "chunk_end_sequence": int(manifest_row.chunk_end_sequence),
            },
            log=log,
        ) as metric:
            diarization_result = diarize_audio(clip_path, config=merged_config)
            metric["payload"]["result_available"] = diarization_result is not None

        error_message = None
        if diarization_result is None:
            error_message = "Catch-up diarization returned no result"

        window_result = _persist_catch_up_diarization_window(
            session,
            recording_id=recording.id,
            manifest_row=manifest_row,
            processing_run_id=processing_run_id,
            diarization_result=diarization_result,
            merged_config=merged_config,
            device=device,
            error_message=error_message,
        )

        manifest_row.diarization_processing_run_id = processing_run_id
        manifest_row.diarization_config_hash = config_hash
        manifest_row.diarization_window_result_id = window_result.id
        manifest_row.processing_run_id = processing_run_id
        if error_message:
            manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_FAILED
            manifest_row.diarization_last_error = error_message
            manifest_row.status = WINDOW_STATUS_FAILED
            manifest_row.last_error = error_message
            failed_window_ids.add(int(manifest_row.id))
        else:
            manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_PROCESSED
            manifest_row.diarization_last_error = None
            manifest_row.last_error = None
            completed_window_ids.add(int(manifest_row.id))
        session.add(manifest_row)

    return completed_window_ids, failed_window_ids


def _build_automatic_meeting_intelligence_transcript(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
    unresolved_speakers: Sequence[str],
) -> str:
    unresolved_labels = set(unresolved_speakers)
    lines: list[str] = []

    for seg in segments:
        speaker_label = str(seg.get("speaker", "Unknown"))
        display_name = (
            speaker_label
            if speaker_label in unresolved_labels
            else speaker_map.get(speaker_label, speaker_label)
        )

        overlapping_names = []
        for overlapping_label in seg.get("overlapping_speakers", []):
            normalized_label = str(overlapping_label)
            if normalized_label in unresolved_labels:
                overlapping_names.append(normalized_label)
            else:
                overlapping_names.append(
                    speaker_map.get(normalized_label, normalized_label)
                )

        overlapping_suffix = (
            f" (with {', '.join(overlapping_names)})" if overlapping_names else ""
        )
        text = str(seg.get("text", "")).strip()
        lines.append(
            f"[{_format_recording_timestamp(seg.get('start', 0))} - "
            f"{_format_recording_timestamp(seg.get('end', seg.get('start', 0)))}] "
            f"{display_name}{overlapping_suffix}: {text}"
        )

    return "\n".join(lines)


def _apply_automatic_meeting_intelligence_result(
    session,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    result: AutomaticMeetingIntelligenceResult,
    *,
    meeting_context: MeetingEventContext | None,
    provider: str | None,
) -> None:
    segments = [
        dict(segment)
        for segment in (transcript.segments or [])
        if isinstance(segment, dict)
    ]
    eligible_labels = get_speakers_eligible_for_llm_renaming(speakers)
    llm_result = build_mapping_based_speaker_suggestions(
        result.speaker_mapping,
        segments=segments,
        eligible_labels=eligible_labels,
        meeting_context=meeting_context,
        source="llm",
    )

    suggestion_count = 0
    suggestion_count += _persist_generated_speaker_name_suggestions(
        session,
        recording=recording,
        transcript=transcript,
        speakers=speakers,
        inference_result=llm_result,
        origin="automatic_meeting_intelligence",
        provider=provider,
        replaced_reason="automatic_meeting_intelligence_refresh",
    )
    superseded_count = _supersede_pending_speaker_name_suggestions_for_labels(
        session,
        transcript=transcript,
        diarization_labels=(
            label for label in eligible_labels if label not in llm_result.mapping
        ),
        reason="automatic_meeting_intelligence_omitted_by_llm",
    )

    recording.name = result.title
    transcript.notes = result.notes_markdown
    transcript.notes_status = "completed"
    transcript.error_message = None
    session.add(recording)
    session.add(transcript)
    session.commit()
    record_pipeline_metric(
        stage="speaker_name_suggestions_generated",
        recording_id=recording.id,
        payload={
            "origin": "automatic_meeting_intelligence",
            "suggestion_count": suggestion_count,
            "superseded_count": superseded_count,
            "rule_based_count": 0,
            "llm_count": len(llm_result.suggestions),
        },
        log=logger,
    )
    update_recording_status(session, recording.id)


def _resolve_meeting_event_context(
    session,
    recording: Recording,
) -> MeetingEventContext | None:
    """Load the linked calendar event for a recording and build its context.

    Returns ``None`` when no event is linked, so the prompt paths fall back to
    the unchanged "no context" string.
    """
    if recording.calendar_event_id is None:
        return None
    try:
        event = session.get(CalendarEvent, recording.calendar_event_id)
        return meeting_event_context_from_calendar_event(event)
    except Exception:
        logger.exception(
            "Failed to load calendar event context for recording %s", recording.id
        )
        return None


def _set_meeting_edge_state(
    session,
    transcript: Transcript,
    *,
    status: str,
    error_message: str | None = None,
    source_signature: str | None = None,
    payload: dict | None = None,
) -> None:
    transcript.meeting_edge_status = status
    transcript.meeting_edge_error_message = error_message
    if source_signature is not None:
        transcript.meeting_edge_source_signature = source_signature
    if payload is not None:
        transcript.meeting_edge_payload = payload
        flag_modified(transcript, "meeting_edge_payload")
    session.add(transcript)
    session.commit()


def _run_automatic_meeting_intelligence_stage(
    *,
    session,
    task: Task | None,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    transcript_text: str,
    unresolved_speakers: Sequence[str],
    llm_config: ResolvedLLMConfig,
    prefer_short_titles: bool,
    device_suffix: str,
) -> AutomaticMeetingIntelligenceResult | None:
    cleaned_transcript = transcript_text.strip()
    meeting_context = _resolve_meeting_event_context(session, recording)
    deterministic_result = detect_rule_based_speaker_suggestions(
        [
            dict(segment)
            for segment in (transcript.segments or [])
            if isinstance(segment, dict)
        ],
        unresolved_speakers,
        meeting_context,
    )
    if not cleaned_transcript:
        suggestion_count = _persist_generated_speaker_name_suggestions(
            session,
            recording=recording,
            transcript=transcript,
            speakers=speakers,
            inference_result=deterministic_result,
            origin="automatic_meeting_intelligence",
            provider=None,
            replaced_reason="automatic_meeting_intelligence_refresh",
        )
        if suggestion_count:
            session.commit()
            record_pipeline_metric(
                stage="speaker_name_suggestions_generated",
                recording_id=recording.id,
                payload={
                    "origin": "automatic_meeting_intelligence",
                    "suggestion_count": suggestion_count,
                    "rule_based_count": len(deterministic_result.suggestions),
                    "llm_count": 0,
                },
                log=logger,
            )
        logger.info(
            "Skipping automatic meeting intelligence for recording %s: transcript is empty",
            recording.id,
        )
        return None

    missing_llm_config = llm_config.missing_configuration_message()
    if missing_llm_config:
        logger.warning(
            "Skipping automatic meeting intelligence for recording %s: %s",
            recording.id,
            missing_llm_config,
        )
        suggestion_count = _persist_generated_speaker_name_suggestions(
            session,
            recording=recording,
            transcript=transcript,
            speakers=speakers,
            inference_result=deterministic_result,
            origin="automatic_meeting_intelligence",
            provider=None,
            replaced_reason="automatic_meeting_intelligence_refresh",
        )
        if suggestion_count:
            session.commit()
            record_pipeline_metric(
                stage="speaker_name_suggestions_generated",
                recording_id=recording.id,
                payload={
                    "origin": "automatic_meeting_intelligence",
                    "suggestion_count": suggestion_count,
                    "rule_based_count": len(deterministic_result.suggestions),
                    "llm_count": 0,
                },
                log=logger,
            )
        return None

    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=cleaned_transcript,
        unresolved_speakers=tuple(unresolved_speakers),
        user_notes=transcript.user_notes,
        prefer_short_titles=prefer_short_titles,
        meeting_context=meeting_context,
    )

    if task is not None:
        task.update_state(
            state="PROCESSING",
            meta={
                "progress": AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS,
                "stage": AUTOMATIC_MEETING_INTELLIGENCE_STAGE,
            },
        )

    recording.processing_step = f"{AUTOMATIC_MEETING_INTELLIGENCE_STEP}{device_suffix}"
    recording.processing_progress = AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS
    transcript.notes_status = "generating"
    transcript.error_message = None
    session.add(recording)
    session.add(transcript)
    session.commit()
    update_recording_status(session, recording.id)

    try:
        llm = _llm_backend_from_config(llm_config)
        result = llm.generate_meeting_intelligence(
            request,
            timeout=AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS,
        )
        _apply_automatic_meeting_intelligence_result(
            session,
            recording,
            transcript,
            speakers,
            result,
            meeting_context=meeting_context,
            provider=llm_config.provider,
        )
        logger.info(
            "Generated unified meeting intelligence for recording %s",
            recording.id,
        )
        return result
    except Exception as exc:
        logger.error(
            "Failed to generate automatic meeting intelligence for recording %s: %s",
            recording.id,
            exc,
        )
        _mark_notes_generation_error(session, recording, transcript, exc)
        return None

class DatabaseTask(Task):
    _session = None

    @property
    def session(self):
        if self._session is None:
            self._session = get_sync_session()
        return self._session

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        if self._session:
            self._session.close()


@celery_app.task(base=DatabaseTask, bind=True)
def refresh_meeting_edge_task(self, recording_id: int):
    session = self.session

    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            return None

        if recording.status not in {
            RecordingStatus.UPLOADING,
            RecordingStatus.QUEUED,
            RecordingStatus.PROCESSING,
        }:
            return None

        transcript = session.exec(
            select(Transcript)
            .where(Transcript.recording_id == recording_id)
            .with_for_update()
        ).first()
        if transcript is None:
            return None

        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings

        if not is_meeting_edge_enabled(user_settings):
            if (
                transcript.meeting_edge_status != MEETING_EDGE_STATUS_IDLE
                or transcript.meeting_edge_error_message
            ):
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        segments = [
            dict(segment)
            for segment in build_transcript_segments_for_read(
                session,
                recording_id,
                transcript=transcript,
            )
            if str(segment.get("text", "")).strip()
        ]
        focus_text = transcript.meeting_edge_focus
        user_notes = transcript.user_notes

        if not segments:
            if transcript.meeting_edge_status != MEETING_EDGE_STATUS_IDLE:
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        segment_count = len(segments)
        word_count = _count_meeting_edge_words(segments)
        if not _has_meeting_edge_signal(
            segment_count=segment_count,
            word_count=word_count,
            focus_text=focus_text,
        ):
            if transcript.meeting_edge_status not in {
                MEETING_EDGE_STATUS_IDLE,
                MEETING_EDGE_STATUS_READY,
            }:
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        llm_config = resolve_llm_config(
            session,
            user_settings,
            purpose=LLM_PURPOSE_MEETING_EDGE,
        )
        config_signature = ":".join(
            [
                llm_config.provider,
                llm_config.model or "",
                llm_config.api_url or "",
            ]
        )

        speakers = session.exec(
            select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
        ).all()
        speaker_map = build_recording_speaker_map(speakers)
        recent_transcript = _build_recent_meeting_edge_transcript(segments, speaker_map)
        source_signature = _build_meeting_edge_source_signature(
            recent_transcript=recent_transcript,
            focus_text=focus_text,
            user_notes=user_notes,
            config_signature=config_signature,
        )

        if not _should_refresh_meeting_edge(
            transcript=transcript,
            source_signature=source_signature,
            current_segment_count=segment_count,
            current_word_count=word_count,
            focus_text=focus_text,
            user_notes=user_notes,
        ):
            return None

        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            _set_meeting_edge_state(
                session,
                transcript,
                status=MEETING_EDGE_STATUS_ERROR,
                error_message=missing_llm_config,
                source_signature=source_signature,
            )
            return None

        previous_payload = (
            transcript.meeting_edge_payload if isinstance(transcript.meeting_edge_payload, dict) else {}
        )
        context_level = get_meeting_edge_context_level(user_settings)
        request = MeetingEdgeRequest(
            recent_transcript=recent_transcript,
            rolling_summary=(previous_payload or {}).get("summary"),
            focus_text=focus_text,
            user_notes=user_notes,
            meeting_context=_resolve_meeting_event_context(session, recording),
            context_level=context_level,
        )

        _set_meeting_edge_state(
            session,
            transcript,
            status=MEETING_EDGE_STATUS_UPDATING,
            error_message=None,
            source_signature=source_signature,
        )

        llm = _llm_backend_from_config(llm_config)
        result = llm.generate_meeting_edge(
            request,
            timeout=MEETING_EDGE_TIMEOUT_SECONDS,
        )
        payload = serialize_meeting_edge_result(result)
        payload.update(
            {
                "generated_at": utc_now().isoformat(),
                "source_segment_count": segment_count,
                "source_word_count": word_count,
                "source_last_end": float(segments[-1].get("end", 0.0)),
                "focus_hash": _hash_meeting_edge_text(focus_text),
                "user_notes_hash": _hash_meeting_edge_text(user_notes),
                "context_level": context_level,
            }
        )
        previous_context_level_value = previous_payload.get(
            "context_level",
            MEETING_EDGE_CONTEXT_LEVEL_MAX if previous_payload else None,
        )
        try:
            previous_context_level = int(previous_context_level_value)
        except (TypeError, ValueError):
            previous_context_level = MEETING_EDGE_CONTEXT_LEVEL_MAX if previous_payload else None
        payload["concept_history"] = merge_meeting_edge_concept_history(
            previous_payload,
            payload,
            reset_history=previous_context_level is not None and previous_context_level > context_level,
        )
        _set_meeting_edge_state(
            session,
            transcript,
            status=MEETING_EDGE_STATUS_READY,
            error_message=None,
            source_signature=source_signature,
            payload=payload,
        )
        return payload
    except Exception as exc:
        logger.error(
            "Meeting Edge refresh failed for recording %s: %s",
            recording_id,
            exc,
            exc_info=True,
        )

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        if transcript is not None:
            _set_meeting_edge_state(
                session,
                transcript,
                status=MEETING_EDGE_STATUS_ERROR,
                error_message=str(exc).strip()[:500] or "Meeting Edge could not be updated.",
            )
        return None

@celery_app.task(base=DatabaseTask, bind=True, autoretry_for=(ConnectionError, urllib.error.URLError, requests.exceptions.RequestException), retry_backoff=True, max_retries=3)
def process_recording_task(self, recording_id: int, force_title_regeneration: bool = False, engine_override: dict | None = None):
    """
    Full processing pipeline: VAD -> Transcribe -> Diarize -> Save
    """
    from backend.processing.vad import mute_non_speech_segments
    from backend.processing.audio_preprocessing import convert_wav_to_mp3, preprocess_audio_for_vad, validate_audio_file, cleanup_temp_file, repair_audio_file
    from backend.processing.transcribe import transcribe_audio
    from backend.processing.diarize import diarize_audio
    from backend.processing.embedding_core import extract_embeddings
    from backend.processing.embedding import cosine_similarity, merge_embeddings, find_matching_global_speaker, AUTO_UPDATE_THRESHOLD
    from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
    from backend.utils.audio import convert_to_mp3, convert_to_proxy_mp3, get_audio_duration
    from backend.utils.live_transcript import (
        apply_live_authority_to_segments,
        build_transcription_result_from_segments,
        merge_reusable_segments,
        map_final_speakers_to_live_labels,
    )

    config_manager.reload()
    
    start_time = time.time()
    session = self.session
    temp_files = []
    catch_up_run: ProcessingRun | None = None
    catch_up_processed_window_ids: set[int] = set()
    catch_up_failed_window_ids: set[int] = set()
    
    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found.")
        return
    
    # Check if cancelled
    if recording.status == RecordingStatus.CANCELLED:
         logger.info(f"Recording {recording_id} was cancelled. Aborting task.")
         return

    user_settings = {}
    if recording.user_id:
        user = session.get(User, recording.user_id)
        if user and user.settings:
            user_settings = user.settings
            logger.info(f"Loaded settings for user {user.username}: {list(user_settings.keys())}")
            
    llm_config = resolve_llm_config(session, user_settings)
    merged_config = llm_config.merged_config
    live_segments_for_reuse = []
    if engine_override is None:
        if config_manager.get("enable_canonical_transcript_writes", True):
            live_segments_for_reuse = build_reusable_live_segments(session, recording.id)
        if not live_segments_for_reuse:
            initial_transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording.id)
            ).first()
            if initial_transcript and initial_transcript.segments:
                live_segments_for_reuse = [
                    dict(segment)
                    for segment in initial_transcript.segments
                    if segment.get("segment_source") in {"live", "catch_up"}
                    or segment.get("provisional") is True
                ]
    
    # Platform/Device detection for UX
    import torch
    device_type = "cpu"
    if config_manager.get("use_gpu", True) and torch.cuda.is_available():
        device_type = "cuda"
    
    # "Gentle" warning suffix
    device_suffix = " (GPU)" if device_type == "cuda" else " (CPU, may take a while)"

    try:
        recording.status = RecordingStatus.PROCESSING
        recording.processing_progress = 20
        if recording.processing_started_at is None or recording.processing_completed_at is not None:
            recording.processing_started_at = utc_now()
        recording.processing_completed_at = None
        session.add(recording)
        session.commit()
        session.refresh(recording)
        
        audio_path = recording.audio_path
        if not audio_path or not os.path.exists(audio_path):
            if recording.proxy_path and os.path.exists(recording.proxy_path):
                logger.info("Source audio missing, but proxy exists. Restoring from proxy...")
                from backend.utils.audio import convert_to_wav
                
                if not audio_path:
                    base_path, _ = os.path.splitext(recording.proxy_path)
                    audio_path = f"{base_path}.wav"
                    recording.audio_path = audio_path
                
                recording.processing_step = f"Restoring audio from proxy...{device_suffix}"
                session.add(recording)
                session.commit()
                
                if convert_to_wav(recording.proxy_path, audio_path):
                    logger.info("Successfully restored source audio from proxy.")
                else:
                    raise FileNotFoundError(f"Source audio missing and failed to restore from proxy.")
            else:
                raise FileNotFoundError(f"Audio file not found: {audio_path} and no proxy available.")

        try:
            validate_audio_file(audio_path)
        except AudioFormatError as e:
            logger.warning(f"Invalid audio file detected: {e}. Attempting repair...")
            repaired_path = repair_audio_file(audio_path)
            
            if repaired_path:
                logger.info(f"Using repaired audio file: {repaired_path}")
                audio_path = repaired_path
                temp_files.append(repaired_path) # Ensure cleanup
            else:
                logger.error(f"Audio repair failed for {audio_path}")
                recording.status = RecordingStatus.ERROR
                recording.processing_step = f"Invalid audio (Repair failed): {str(e)}"
                session.add(recording)
                session.commit()
                return

        # Fix missing duration if needed
        if (not recording.duration_seconds or recording.duration_seconds == 0):
            try:
                duration = get_audio_duration(audio_path)
                recording.duration_seconds = duration
                session.add(recording)
                session.commit()
                session.refresh(recording)
            except Exception as e:
                logger.warning(f"Could not determine duration for recording {recording_id}: {e}")
    
        # --- VAD Stage ---
        enable_vad = merged_config.get("enable_vad", True)
        
        if enable_vad:
            self.update_state(state='PROCESSING', meta={'progress': 30, 'stage': 'VAD'})
            recording.processing_step = f"Filtering silence and noise...{device_suffix}"
            recording.processing_progress = 30
            session.add(recording)
            session.commit()
            
            # Preprocess for VAD (resample to 16k mono)
            vad_input_path = preprocess_audio_for_vad(audio_path)
            if not vad_input_path:
                raise RuntimeError("VAD preprocessing failed")
            temp_files.append(vad_input_path)
                
            # Run VAD (mute silence)
            vad_output_path = vad_input_path.replace("_vad.wav", "_vad_processed.wav")
            vad_success, speech_duration = mute_non_speech_segments(vad_input_path, vad_output_path)
            
            if not vad_success:
                 raise RuntimeError("VAD execution failed")
            temp_files.append(vad_output_path)

            # Check for silence
            if speech_duration < 1.0:
                logger.warning(f"No speech detected in recording {recording_id} (speech duration: {speech_duration}s)")
                recording.status = RecordingStatus.PROCESSED
                recording.client_status = ClientStatus.IDLE
                recording.processing_step = "Completed (No speech detected)"
                recording.processing_completed_at = utc_now()

                # Create empty transcript
                transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording.id)).first()
                if not transcript:
                    transcript = Transcript(recording_id=recording.id)

                transcript.text = ""  # Empty string to prevent hallucinations
                transcript.segments = []
                transcript.transcript_status = "completed"

                mark_recording_audio_chunks_ready_for_cleanup(
                    session,
                    recording_id=recording.id,
                    upload_status="finalized",
                )
                auto_link_recording(session, recording)
                session.add(transcript)
                session.add(recording)
                session.commit()
                return

            # Use WAV for processing to avoid sample count mismatches in Pyannote
            processed_audio_path = vad_output_path
        else:
            logger.info("VAD disabled, skipping silence filtering.")
            # Still need to preprocess to ensure 16k mono wav for Whisper/Pyannote
            vad_input_path = preprocess_audio_for_vad(audio_path)
            if not vad_input_path:
                raise RuntimeError("Audio preprocessing failed")
            temp_files.append(vad_input_path)
            processed_audio_path = vad_input_path
            pass

        logger.info(f"Using processed audio for transcription/diarization: {processed_audio_path}")
        if not os.path.exists(processed_audio_path):
             raise FileNotFoundError(f"Processed audio file missing: {processed_audio_path}")
        
        # --- Transcription Stage ---
        self.update_state(state='PROCESSING', meta={'progress': 50, 'stage': 'Transcription'})
        recording.processing_step = f"Transcribing audio...{device_suffix}"
        recording.processing_progress = 50
        session.add(recording)
        session.commit()
        
        # Apply per-reprocess transcription-engine override, if provided.
        if engine_override:
            merged_config.update(engine_override)
            logger.info("Reprocess: engine override applied: %s", engine_override)

        transcription_result = None
        reused_live_transcript_segments = []
        pending_manifest_rows = []
        if engine_override is None:
            pending_manifest_rows = [
                row
                for row in _load_recording_audio_window_manifests(session, recording.id)
                if row.id is not None
                and not window_asr_is_processed(row)
            ]
        if pending_manifest_rows:
            from backend.utils.audio import extract_audio_clip

            self.update_state(state='PROCESSING', meta={'progress': 45, 'stage': 'Catch-up'})
            recording.processing_step = f"Catching up live audio...{device_suffix}"
            recording.processing_progress = 45
            session.add(recording)
            session.commit()

            catch_up_segments, pending_catch_up_window_ids, catch_up_run = _build_catch_up_segments(
                session=session,
                recording=recording,
                processed_audio_path=processed_audio_path,
                merged_config=merged_config,
                transcribe_audio=transcribe_audio,
                extract_audio_clip=extract_audio_clip,
                temp_files=temp_files,
                log=logger,
            )
            live_segments_for_reuse = merge_reusable_segments(
                live_segments_for_reuse,
                catch_up_segments,
            )
            if pending_catch_up_window_ids:
                manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
                updated_manifest_rows = mark_audio_windows_processed(
                    manifest_rows,
                    window_ids=pending_catch_up_window_ids,
                    status=WINDOW_STATUS_CATCH_UP_PROCESSED,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                )
                for manifest_row in updated_manifest_rows:
                    session.add(manifest_row)
                session.commit()
            record_pipeline_metric(
                stage="catch_up_segments_prepared",
                recording_id=recording_id,
                payload={
                    "segment_count": len(catch_up_segments),
                    "window_count": len(pending_catch_up_window_ids),
                },
                log=logger,
            )
        if engine_override is None and merged_config.get("enable_diarization", True):
            from backend.utils.audio import extract_audio_clip

            self.update_state(state='PROCESSING', meta={'progress': 48, 'stage': 'Catch-up diarization'})
            recording.processing_step = f"Catching up speaker windows...{device_suffix}"
            recording.processing_progress = 48
            session.add(recording)
            session.commit()

            (
                catch_up_processed_window_ids,
                catch_up_failed_window_ids,
            ) = _run_catch_up_diarization_windows(
                session=session,
                recording=recording,
                processed_audio_path=processed_audio_path,
                merged_config=merged_config,
                diarize_audio=diarize_audio,
                extract_audio_clip=extract_audio_clip,
                processing_run_id=catch_up_run.id if catch_up_run else None,
                temp_files=temp_files,
                log=logger,
            )
            record_pipeline_metric(
                stage="catch_up_diarization_recorded",
                recording_id=recording_id,
                payload={
                    "processed_window_count": len(catch_up_processed_window_ids),
                    "failed_window_count": len(catch_up_failed_window_ids),
                },
                status="error" if catch_up_failed_window_ids else "ok",
                log=logger,
            )
        elif pending_manifest_rows:
            catch_up_processed_window_ids = set(pending_catch_up_window_ids)

        if live_segments_for_reuse and engine_override is None:
            transcription_result, reused_live_transcript_segments = (
                build_transcription_result_from_segments(live_segments_for_reuse)
            )
            if transcription_result:
                recording.processing_step = f"Reusing live transcript...{device_suffix}"
                session.add(recording)
                session.commit()
                record_pipeline_metric(
                    stage="final_transcription_reused_live",
                    recording_id=recording_id,
                    payload={
                        "segment_count": len(reused_live_transcript_segments),
                        "text_chars": len(transcription_result.get("text", "")),
                    },
                    log=logger,
                )
                logger.info(
                    "Reusing %s live transcript segments for recording %s",
                    len(reused_live_transcript_segments),
                    recording_id,
                )

        if not transcription_result:
            # Run the configured transcription engine.
            with pipeline_metric_timer(
                stage="final_asr_invocation",
                recording_id=recording_id,
                payload={
                    "engine": merged_config.get("transcription_backend"),
                    "engine_override": bool(engine_override),
                    "input_path": processed_audio_path,
                },
                log=logger,
            ) as metric:
                asr_source_kind = "reprocess" if engine_override else "finalize"
                span_end_ms = int(round(float(recording.duration_seconds or 0.0) * 1000.0))
                if config_manager.get("enable_asr_window_result_ledger", True):
                    start_recording_asr_window_result(
                        session,
                        recording_id=recording.id,
                        source_kind=asr_source_kind,
                        span_start_ms=0,
                        span_end_ms=span_end_ms,
                        config=merged_config,
                        config_hash=_final_asr_config_hash(merged_config),
                    )
                try:
                    transcription_result = transcribe_audio(processed_audio_path, config=merged_config)
                except Exception as exc:
                    if config_manager.get("enable_asr_window_result_ledger", True):
                        fail_recording_asr_window_result(
                            session,
                            recording_id=recording.id,
                            source_kind=asr_source_kind,
                            span_start_ms=0,
                            span_end_ms=span_end_ms,
                            config=merged_config,
                            config_hash=_final_asr_config_hash(merged_config),
                            error_summary=str(exc).strip()[:500] or "Final ASR invocation failed.",
                            error_payload={"error_type": exc.__class__.__name__},
                        )
                    raise
                if config_manager.get("enable_asr_window_result_ledger", True):
                    if transcription_result is None:
                        fail_recording_asr_window_result(
                            session,
                            recording_id=recording.id,
                            source_kind=asr_source_kind,
                            span_start_ms=0,
                            span_end_ms=span_end_ms,
                            config=merged_config,
                            config_hash=_final_asr_config_hash(merged_config),
                            error_summary="Final ASR returned no result.",
                            error_payload={"error_type": "empty_result"},
                        )
                    else:
                        complete_recording_asr_window_result(
                            session,
                            recording_id=recording.id,
                            source_kind=asr_source_kind,
                            span_start_ms=0,
                            span_end_ms=span_end_ms,
                            config=merged_config,
                            config_hash=_final_asr_config_hash(merged_config),
                            result_payload={
                                "segment_count": len((transcription_result or {}).get("segments", [])),
                                "text_chars": len((transcription_result or {}).get("text") or ""),
                                "engine_override": bool(engine_override),
                            },
                        )
                metric["payload"]["segment_count"] = len(
                    (transcription_result or {}).get("segments", [])
                )
        
        # --- Diarization Stage ---
        enable_diarization = merged_config.get("enable_diarization", True)
        diarization_result = None
        final_diarization_plan = _build_final_diarization_plan(
            live_segments_for_reuse=live_segments_for_reuse,
            reused_live_transcript_segments=reused_live_transcript_segments,
            engine_override=engine_override,
            completed_window_replay_available=_recording_has_completed_diarization_windows(
                session,
                recording_id=recording.id,
            ),
        )
        
        if enable_diarization and final_diarization_plan["should_run"] is True:
            self.update_state(state='PROCESSING', meta={'progress': 70, 'stage': 'Diarization'})
            recording.processing_step = f"Determining who said what...{device_suffix}"
            recording.processing_progress = 70
            session.add(recording)
            session.commit()
            
            # Run Pyannote
            with pipeline_metric_timer(
                stage="final_diarization_invocation",
                recording_id=recording_id,
                payload={
                    "input_path": processed_audio_path,
                    "enabled": True,
                    "reason": str(final_diarization_plan["reason"]),
                    "low_confidence_span_count": len(final_diarization_plan["low_confidence_spans"]),
                    "low_confidence_spans": list(final_diarization_plan["low_confidence_spans"]),
                },
                log=logger,
            ) as metric:
                diarization_result = diarize_audio(processed_audio_path, config=merged_config)
                metric["payload"]["result_available"] = diarization_result is not None

            if diarization_result is None:
                 msg = "Diarization failed (check HF token), falling back to single speaker."
                 logger.warning(msg)
                 recording.processing_step = msg
                 session.add(recording)
                 session.commit()
            else:
                # Post-diarization phantom speaker filter
                from backend.processing.phantom_filter import filter_phantom_speakers
                try:
                    diarization_result = filter_phantom_speakers(
                        diarization_result, processed_audio_path, config=merged_config
                    )
                except Exception as e:
                    logger.warning(f"Phantom speaker filter failed, continuing with unfiltered result: {e}")
        elif enable_diarization:
            logger.info(
                "Skipping final full-recording diarization for recording %s (reason=%s)",
                recording_id,
                final_diarization_plan["reason"],
            )
            record_pipeline_metric(
                stage="final_diarization_skipped",
                recording_id=recording_id,
                payload={
                    "reason": str(final_diarization_plan["reason"]),
                    "low_confidence_span_count": len(final_diarization_plan["low_confidence_spans"]),
                },
                log=logger,
            )
        else:
            logger.info("Diarization disabled, skipping speaker separation.")

        # --- Merge & Save ---
        self.update_state(state='PROCESSING', meta={'progress': 85, 'stage': 'Saving'})
        recording.processing_step = f"Saving transcript...{device_suffix}"
        recording.processing_progress = 85
        session.add(recording)
        session.commit()
        
        # Combine Transcription and Diarization
        combined_segments = []
        if transcription_result:
            # Only attempt combination if we have both results
            if diarization_result:
                combined_segments = combine_transcription_diarization(transcription_result, diarization_result)
            else:
                logger.info("Diarization result missing or disabled. Skipping combination.")
        
        logger.info(f"Combined segments count: {len(combined_segments) if combined_segments else 0}")
        
        if not combined_segments:
            # Fallback if combination fails or was skipped
            if enable_diarization and diarization_result:
                 logger.warning("Combination failed despite having diarization result. Using raw transcription segments with UNKNOWN speaker.")
            else:
                 logger.info("Using raw transcription segments (Diarization disabled or failed).")
            
            # Check if transcription_result is None before accessing
            if transcription_result and 'segments' in transcription_result:
                combined_segments = []
                for seg in transcription_result.get('segments', []):
                    fallback_segment = {
                        "start": seg["start"],
                        "end": seg["end"],
                        "speaker": "UNKNOWN",
                        "text": seg["text"].strip()
                    }
                    if seg.get("id"):
                        fallback_segment["id"] = seg["id"]
                    if seg.get("words"):
                        fallback_segment["words"] = seg["words"]
                    combined_segments.append(fallback_segment)
            else:
                logger.error("Transcription result is None or missing segments during fallback.")
                combined_segments = []

        label_map_from_final_to_live = {}
        if reused_live_transcript_segments and combined_segments:
            if diarization_result:
                label_map_from_final_to_live = map_final_speakers_to_live_labels(
                    live_segments_for_reuse,
                    combined_segments,
                )

            if label_map_from_final_to_live:
                for segment in combined_segments:
                    current_label = segment.get("speaker")
                    if current_label in label_map_from_final_to_live:
                        segment["speaker"] = label_map_from_final_to_live[current_label]
                    if segment.get("overlapping_speakers"):
                        segment["overlapping_speakers"] = [
                            label_map_from_final_to_live.get(label, label)
                            for label in segment.get("overlapping_speakers", [])
                        ]
            combined_segments = apply_live_authority_to_segments(
                live_segments_for_reuse,
                combined_segments,
            )
            record_pipeline_metric(
                stage="final_live_reconciliation",
                recording_id=recording_id,
                payload={
                    "live_segment_count": len(live_segments_for_reuse),
                    "combined_segment_count": len(combined_segments),
                    "mapped_speaker_count": len(label_map_from_final_to_live),
                    "used_diarization": bool(diarization_result),
                    "manual_speaker_edits": sum(
                        1
                        for segment in live_segments_for_reuse
                        if segment.get("speaker_manually_edited") is True
                    ),
                    "preserved_manual_speaker_edits": sum(
                        1
                        for segment in combined_segments
                        if segment.get("speaker_manually_edited") is True
                    ),
                    "manual_text_edits": sum(
                        1
                        for segment in live_segments_for_reuse
                        if segment.get("text_manually_edited") is True
                    ),
                    "preserved_manual_text_edits": sum(
                        1
                        for segment in combined_segments
                        if segment.get("text_manually_edited") is True
                    ),
                },
                log=logger,
            )

        # Consolidate segments
        final_segments = consolidate_diarized_transcript(combined_segments)
        record_pipeline_metric(
            stage="final_segments_built",
            recording_id=recording_id,
            payload={"segment_count": len(final_segments)},
            log=logger,
        )
        logger.info(f"Final segments after consolidation: {len(final_segments)}")
        
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording.id)).first()

        # Create or Update Transcript Record
        # Handle case where transcription_result is None (e.g. due to error)
        full_text = transcription_result.get('text', '') if transcription_result else ''
        
        if transcript:
            transcript.text = full_text
            transcript.segments = final_segments
            transcript.transcript_status = "completed"
            transcript.error_message = None
            if transcript.notes_status == "error":
                transcript.notes_status = "pending"
            session.add(transcript)
        else:
            transcript = Transcript(
                recording_id=recording.id,
                text=full_text,
                segments=final_segments,
                transcript_status="completed"
            )
            session.add(transcript)
        
        session.commit()

        if catch_up_run is not None or catch_up_processed_window_ids or catch_up_failed_window_ids:
            if catch_up_run is not None:
                if catch_up_failed_window_ids:
                    catch_up_run.status = ProcessingRunStatus.FAILED
                    catch_up_run.error_summary = (
                        f"{len(catch_up_failed_window_ids)} catch-up diarization window(s) failed"
                    )
                else:
                    catch_up_run.status = ProcessingRunStatus.COMPLETED
                    catch_up_run.error_summary = None
                catch_up_run.completed_at = utc_now()
                session.add(catch_up_run)
            session.commit()
        # update_recording_status(session, recording.id) # Removed to prevent premature status update (flash)
        
        # Save Speakers & Embeddings
        # Processes speakers in order of appearance to assign "Speaker 1", "Speaker 2", etc.
        ordered_speakers = _collect_ordered_final_speaker_labels(final_segments)
        
        logger.info(f"Extracted {len(ordered_speakers)} unique speakers from segments: {ordered_speakers}")
        
        # Extract embeddings for all speakers in the diarization result (if enabled)
        # Voiceprint extraction can be disabled to speed up processing
        enable_auto_voiceprints = merged_config.get("enable_auto_voiceprints", True)
        speaker_embeddings = {}
        
        if enable_auto_voiceprints and diarization_result:
            self.update_state(state='PROCESSING', meta={'progress': 90, 'stage': 'Voiceprints'})
            recording.processing_step = f"Learning voiceprints...{device_suffix}"
            recording.processing_progress = 90
            session.add(recording)
            session.commit()
            logger.info("Extracting speaker voiceprints (enable_auto_voiceprints=True)")
            speaker_embeddings = extract_embeddings(processed_audio_path, diarization_result, device_str=merged_config.get("processing_device", "cpu"), config=merged_config)
            if label_map_from_final_to_live:
                speaker_embeddings = {
                    label_map_from_final_to_live.get(label, label): embedding
                    for label, embedding in speaker_embeddings.items()
                }
        elif not enable_auto_voiceprints:
            logger.info("Skipping voiceprint extraction (enable_auto_voiceprints=False)")
        
        # Map local labels (SPEAKER_00) to resolved names (John Doe or Speaker 1)
        label_map = {} 
        speaker_counter = 1
        
        # Track which names have been assigned to which speaker ID/Label to detect duplicates
        # Format: name -> {'id': recording_speaker_id, 'label': diarization_label}
        resolved_names_map = {}
        
        for label in ordered_speakers:
            # Check if speaker already exists for this recording (idempotency)
            existing_speaker = session.exec(
                select(RecordingSpeaker)
                .where(RecordingSpeaker.recording_id == recording.id)
                .where(RecordingSpeaker.diarization_label == label)
            ).first()
            
            embedding = speaker_embeddings.get(label)
            resolved_name = label # Default fallback
            global_speaker_id = None
            is_identified = False
            
            # --- LOGIC UPDATE: Check for Manual Names & Merges ---
            if existing_speaker:
                # 1. Check if this speaker was merged into another
                if existing_speaker.merged_into_id:
                    logger.info(f"Speaker {label} is merged. Resolving target...")
                    current_spk = existing_speaker
                    visited_ids = {current_spk.id}
                    
                    # Follow the merge chain (prevent infinite loops)
                    while current_spk.merged_into_id:
                        next_spk = session.get(RecordingSpeaker, current_spk.merged_into_id)
                        if not next_spk:
                            logger.warning(f"Merge chain broken for speaker {label} at ID {current_spk.merged_into_id}")
                            break
                        if next_spk.id in visited_ids:
                            logger.warning(f"Circular merge detected for speaker {label}")
                            break
                        visited_ids.add(next_spk.id)
                        current_spk = next_spk
                    
                    # Use the target speaker's name
                    resolved_name = current_spk.name or current_spk.local_name or current_spk.diarization_label
                    logger.info(f"Resolved {label} (Merged) -> {resolved_name}")
                    if current_spk.global_speaker_id:
                        global_speaker_id = current_spk.global_speaker_id
                        is_identified = True # Don't re-identify
                    else:
                        # It's a local merge, so we trust the local name
                        is_identified = True 
                
                # 2. Check for manual rename (if not merged)
                elif existing_speaker.local_name:
                    resolved_name = existing_speaker.local_name
                    logger.info(f"Preserving manual name for {label}: {existing_speaker.local_name}")
                    is_identified = True # Skip inference
                    
                    if existing_speaker.global_speaker_id:
                         global_speaker_id = existing_speaker.global_speaker_id

            # Try to identify speaker using embedding (ONLY if not manually named/merged)
            if not is_identified and embedding:
                # Fetch all global speakers with embeddings belonging to this user
                # Filter out any potential placeholder names from the global list to prevent bad linking
                all_global_speakers = session.exec(
                    select(GlobalSpeaker)
                    .where(GlobalSpeaker.embedding != None)
                    .where(GlobalSpeaker.user_id == recording.user_id)
                ).all()
                
                import re
                placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
                
                global_speakers = [
                    gs for gs in all_global_speakers 
                    if not placeholder_pattern.match(gs.name) and gs.embedding and len(gs.embedding) > 0 and not any(x is None for x in gs.embedding)
                ]
                
                # Use centralized matching logic with 0.75 threshold and margin of victory
                best_match, best_score = find_matching_global_speaker(
                    embedding, 
                    global_speakers,
                    threshold=0.75,
                    margin=0.05
                )
                
                if best_match:
                    logger.info(f"Identified {label} as {best_match.name} (Score: {best_score:.2f})")
                    resolved_name = best_match.name
                    global_speaker_id = best_match.id
                    is_identified = True

                    # Active Learning: only update the global embedding when the
                    # match confidence is high enough to avoid polluting it with
                    # borderline or false-positive identifications.
                    if not best_match.is_voiceprint_locked and best_score >= AUTO_UPDATE_THRESHOLD:
                        try:
                            new_emb = merge_embeddings(best_match.embedding, embedding)
                            best_match.embedding = new_emb
                            session.add(best_match)
                        except Exception as e:
                            logger.warning(f"Failed to update embedding for {best_match.name}: {e}")
                    elif not best_match.is_voiceprint_locked:
                        logger.info(
                            f"Skipping auto-update for {best_match.name} "
                            f"(score {best_score:.2f} < auto-update threshold {AUTO_UPDATE_THRESHOLD})"
                        )
                else:
                    logger.info(f"No match found for {label} (Best score: {best_score:.2f}).")

            # If not identified as a global speaker, assign a friendly sequential name
            if not is_identified:
                resolved_name = f"Speaker {speaker_counter}"
                speaker_counter += 1

            # Auto-promotion logic removed. Speakers must be manually promoted.

            # Auto-merge duplicate name detection: if this resolved name was already
            # assigned to a previous speaker in this loop, merge into the existing one.
            if resolved_name and resolved_name in resolved_names_map:
                target_info = resolved_names_map[resolved_name]
                target_label = target_info['label']
                target_id = target_info['id']
                
                if target_label != label:
                    logger.info(f"Auto-Merge: '{resolved_name}' already assigned to {target_label}. Merging {label} into {target_label}.")
                    
                    if existing_speaker:
                        existing_speaker.merged_into_id = target_id
                        existing_speaker.name = resolved_name # Keep consistent name
                        existing_speaker.local_name = None 
                        session.add(existing_speaker)
                        session.flush() # Ensure it's saved
                    else:
                        # Create the record but immediately merge it
                        rec_speaker = RecordingSpeaker(
                            recording_id=recording.id,
                            diarization_label=label,
                            name=resolved_name,
                            embedding=embedding,
                            global_speaker_id=global_speaker_id,
                            merged_into_id=target_id
                        )
                        session.add(rec_speaker)
                        session.flush() 
                    
                    # rewrite segments in memory to point to the target label
                    # This ensures the transcript assumes they are the same speaker
                    for seg in final_segments:
                        if seg['speaker'] == label:
                            seg['speaker'] = target_label
                        
                        if 'overlapping_speakers' in seg:
                            for idx, ov_spk in enumerate(seg['overlapping_speakers']):
                                if ov_spk == label:
                                    seg['overlapping_speakers'][idx] = target_label
                            
                    # No addition to resolved_names_map needed; the canonical entry already exists.
                    label_map[label] = resolved_name
                    continue


            label_map[label] = resolved_name
            logger.info(f"Mapped {label} -> {resolved_name}")

            current_speaker_id = None
            if existing_speaker:
                if embedding is not None:
                    existing_speaker.embedding = embedding
                elif existing_speaker.embedding:
                    logger.info(
                        "Preserving existing voiceprint for %s because final diarization produced no embedding.",
                        label,
                    )
                existing_speaker.name = resolved_name
                if global_speaker_id is not None or existing_speaker.global_speaker_id is None:
                    existing_speaker.global_speaker_id = global_speaker_id
                session.add(existing_speaker)
                session.flush()
                current_speaker_id = existing_speaker.id
            else:
                rec_speaker = RecordingSpeaker(
                    recording_id=recording.id,
                    diarization_label=label,
                    name=resolved_name,
                    embedding=embedding,
                    global_speaker_id=global_speaker_id
                )
                session.add(rec_speaker)
                session.flush()
                current_speaker_id = rec_speaker.id
            
            # Register this name as taken
            if resolved_name and current_speaker_id:
                resolved_names_map[resolved_name] = {'id': current_speaker_id, 'label': label}
        
        # Keep the diarization_label in the segments to maintain the link to RecordingSpeaker
        # The frontend will resolve the display name using the speaker map
        updated_segments = []
        for seg in final_segments:
            updated_segments.append(seg)

        self.update_state(state='PROCESSING', meta={'progress': 92, 'stage': 'Finalizing'})
        recording.processing_step = f"Finalizing transcript structure...{device_suffix}"
        recording.processing_progress = 92
        session.add(recording)
        session.commit()
        
        # Log final speaker distribution in updated segments
        final_speaker_counts = {}
        for seg in updated_segments:
            spk = seg['speaker']
            final_speaker_counts[spk] = final_speaker_counts.get(spk, 0) + 1
            for ov_spk in seg.get('overlapping_speakers', []):
                final_speaker_counts[ov_spk] = final_speaker_counts.get(ov_spk, 0) + 1
        logger.info(f"Final transcript speaker distribution: {final_speaker_counts}")
            
        transcript.segments = updated_segments
        session.add(transcript)
        if config_manager.get("enable_canonical_transcript_writes", True):
            finalize_utterances_from_segments(
                session,
                recording_id=recording.id,
                segments=[dict(segment) for segment in updated_segments],
                reused_live_asr=bool(reused_live_transcript_segments),
                trigger_source="worker",
            )
            if not final_diarization_plan["should_run"] and _recording_has_completed_diarization_windows(
                session,
                recording_id=recording.id,
            ):
                replay_summary = reconcile_completed_diarization_windows(
                    session,
                    recording_id=recording.id,
                    effective_from_ms=0,
                    source="finalize_window_replay",
                )
                record_pipeline_metric(
                    stage="final_diarization_window_replay",
                    recording_id=recording_id,
                    payload=replay_summary,
                    log=logger,
                )
                updated_segments = build_transcript_segments_for_read(
                    session,
                    recording.id,
                    transcript=transcript,
                )

            # Phase F4: frame-level segmentation safety net for utterances
            # that span a speaker change but slipped through rolling
            # diarization's coarser turn boundaries.
            try:
                self.update_state(state='PROCESSING', meta={'progress': 94, 'stage': 'Refining'})
                recording.processing_step = f"Refining speaker boundaries...{device_suffix}"
                recording.processing_progress = 94
                session.add(recording)
                session.commit()
                with pipeline_metric_timer(
                    stage="segmentation_refinement",
                    recording_id=recording_id,
                    payload={"input_path": processed_audio_path},
                    log=logger,
                ) as seg_metric:
                    seg_summary = refine_recording_utterances_via_segmentation(
                        session,
                        recording_id=recording.id,
                        audio_path=processed_audio_path,
                        device_str=str(merged_config.get("processing_device", "auto")),
                        hf_token=config_manager.get("hf_token"),
                        source="finalize_segmentation_refinement",
                    )
                    seg_metric["payload"].update(seg_summary)
                if (seg_summary or {}).get("refined_utterance_count", 0) > 0:
                    updated_segments = build_transcript_segments_for_read(
                        session,
                        recording.id,
                        transcript=transcript,
                    )
            except Exception as seg_exc:
                logger.warning(
                    "Segmentation refinement pass failed for recording %s: %s",
                    recording.id,
                    seg_exc,
                    exc_info=True,
                )

        recording_speakers = session.exec(
            select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording.id)
        ).all()
        unresolved_speakers = get_speakers_eligible_for_llm_renaming(recording_speakers)
        speaker_map = build_recording_speaker_map(recording_speakers)
        transcript_text = _build_automatic_meeting_intelligence_transcript(
            updated_segments,
            speaker_map,
            unresolved_speakers,
        )

        _run_automatic_meeting_intelligence_stage(
            session=session,
            task=self,
            recording=recording,
            transcript=transcript,
            speakers=recording_speakers,
            transcript_text=transcript_text,
            unresolved_speakers=unresolved_speakers,
            llm_config=llm_config,
            prefer_short_titles=merged_config.get("prefer_short_titles", True),
            device_suffix=device_suffix,
        )

        # Update Recording Status
        mark_recording_audio_chunks_ready_for_cleanup(
            session,
            recording_id=recording.id,
            upload_status="finalized",
        )
        recording.client_status = ClientStatus.IDLE
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        recording.processing_completed_at = utc_now()
        auto_link_recording(session, recording)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)
        
        # Delete source wav if proxy exists to save storage
        session.refresh(recording)
        if _can_delete_source_audio(recording):
            try:
                logger.info(f"Storage optimization: Proxy audio exists, deleting source audio {recording.audio_path}")
                os.remove(recording.audio_path)
            except Exception as e:
                logger.error(f"Failed to delete source audio {recording.audio_path}: {e}")
        
        elapsed_time = time.time() - float(start_time)
        record_pipeline_metric(
            stage="final_processing_completed",
            recording_id=recording_id,
            payload={"status": "success"},
            elapsed_ms=elapsed_time * 1000.0,
            log=logger,
        )
        logger.info(f"Recording: [{recording_id}] processing succeeded in {elapsed_time:.2f} seconds")
        
        # Trigger Transcript Indexing for RAG
        # Triggers transcript indexing after all data is committed.
        from backend.worker.tasks import index_transcript_task
        index_transcript_task.delay(recording_id)
        
        return {"status": "success", "recording_id": recording_id}

    except AudioProcessingError as e:
        record_pipeline_metric(
            stage="final_processing_failed",
            recording_id=recording_id,
            payload={"error": str(e), "error_type": "AudioProcessingError"},
            status="error",
            log=logger,
        )
        logger.error(f"Audio processing error for {recording_id}: {e}", exc_info=True)
        if hasattr(session, "rollback"):
            try:
                session.rollback()
            except Exception as rollback_exc:
                logger.warning(
                    "Failed to rollback session after audio processing error for %s: %s",
                    recording_id,
                    rollback_exc,
                )
        recording = session.get(Recording, recording_id)
        if recording:
            if catch_up_run is not None:
                catch_up_run.status = ProcessingRunStatus.FAILED
                catch_up_run.error_summary = str(e)
                catch_up_run.completed_at = utc_now()
                session.add(catch_up_run)
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"Error: {str(e)}"
            recording.processing_completed_at = None
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)
            
    except Exception as e:
        record_pipeline_metric(
            stage="final_processing_failed",
            recording_id=recording_id,
            payload={"error": str(e), "error_type": type(e).__name__},
            status="error",
            log=logger,
        )
        logger.error(f"Processing failed for {recording_id}: {e}", exc_info=True)
        if hasattr(session, "rollback"):
            try:
                session.rollback()
            except Exception as rollback_exc:
                logger.warning(
                    "Failed to rollback session after processing error for %s: %s",
                    recording_id,
                    rollback_exc,
                )
        recording = session.get(Recording, recording_id)
        if recording:
            if catch_up_run is not None:
                catch_up_run.status = ProcessingRunStatus.FAILED
                catch_up_run.error_summary = str(e)
                catch_up_run.completed_at = utc_now()
                session.add(catch_up_run)
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"System Error: {str(e)}"
            recording.processing_completed_at = None
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)
            
    finally:
        # Robust cleanup of all temporary files
        for temp_file in temp_files:
            cleanup_temp_file(temp_file)
            
        # --- VRAM Management ---
        # Explicitly release models if configured to do so (default behavior for shared hosts)
        keep_loaded = config_manager.get("keep_models_loaded", False)
        
        if not keep_loaded:
            try:
                logger.info("Releasing VRAM (keep_models_loaded=False)...")
                
                # 1. Whisper
                from backend.processing.transcribe import release_model_cache
                release_model_cache()
                
                # 2. Pyannote
                from backend.processing.diarize import release_pipeline_cache
                release_pipeline_cache()
                
                # 3. Text Embeddings
                from backend.processing.text_embedding import release_embedding_model
                release_embedding_model()
                
                # 4. Garbage Collection
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    
                logger.info("VRAM released successfully.")
            except Exception as e:
                logger.error(f"Error releasing VRAM: {e}")

@celery_app.task(base=DatabaseTask, bind=True)
def update_speaker_embedding_task(self, recording_id: int, start: float, end: float, recording_speaker_id: int):
    """
    Update the speaker embedding for a specific segment (Active Learning).
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    from backend.processing.embedding import merge_embeddings
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        
        target_audio = recording.audio_path
        if not target_audio or not os.path.exists(target_audio):
            if recording.proxy_path and os.path.exists(recording.proxy_path):
                target_audio = recording.proxy_path
            else:
                logger.warning(f"Recording {recording_id} not found or audio missing.")
                return

        target_recording_speaker = session.get(RecordingSpeaker, recording_speaker_id)
        if not target_recording_speaker:
            logger.warning(f"RecordingSpeaker {recording_speaker_id} not found.")
            return

        device = "cuda" if config_manager.get("use_gpu", True) else "cpu"
        
        # Extract embedding for this segment
        # Passes a list of segments [(start, end)] for embedding extraction.
        new_embedding = extract_embedding_for_segments(
            target_audio, 
            [(start, end)], 
            device_str=device
        )

        if new_embedding:
            # Merge into RecordingSpeaker
            current_emb = target_recording_speaker.embedding if target_recording_speaker.embedding is not None else []
            
            target_recording_speaker.embedding = merge_embeddings(
                current_emb, 
                new_embedding, 
                alpha=0.5
            )
            session.add(target_recording_speaker)
            
            # Merge into GlobalSpeaker
            if target_recording_speaker.global_speaker_id:
                gs = session.get(GlobalSpeaker, target_recording_speaker.global_speaker_id)
                if gs:
                    gs_emb = gs.embedding if gs.embedding is not None else []
                    gs.embedding = merge_embeddings(
                        gs_emb,
                        new_embedding,
                        alpha=0.5
                    )
                    session.add(gs)
            
            session.commit()
            logger.info(f"Updated embedding for speaker {target_recording_speaker.diarization_label}")
        else:
            logger.warning("Failed to extract embedding for update.")

    except Exception as e:
        logger.error(f"Failed to update speaker embedding: {e}", exc_info=True)
        session.rollback()

@celery_app.task(bind=True)
def extract_embedding_task(self, audio_path: str, segments: list, device_str: str = "cpu", hf_token: str = None):
    """
    Extract embedding from segments. Used by API for synchronous-like operations.
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    try:
        # If token not passed, try to get from config in worker
        if not hf_token:
            from backend.utils.config_manager import config_manager
            hf_token = config_manager.get("hf_token")
            
        return extract_embedding_for_segments(audio_path, segments, device_str, hf_token)
    except Exception as e:
        logger.error(f"Failed to extract embedding task: {e}", exc_info=True)
        return None

@worker_ready.connect
def check_queued_recordings(sender, **kwargs):
    """
    On worker startup, check for any recordings that are stuck in QUEUED state
    and re-queue them.
    """
    logger.info("Checking for pending QUEUED recordings...")
    session = get_sync_session()
    try:
        statement = select(Recording).where(Recording.status == RecordingStatus.QUEUED)
        recordings = session.exec(statement).all()
        
        if not recordings:
            logger.info("No pending recordings found.")
            return

        logger.info(f"Found {len(recordings)} pending recordings. Re-queueing...")
        
        for recording in recordings:
            logger.info(f"Re-queueing recording {recording.id}: {recording.name}")
            process_recording_task.delay(recording.id) # type: ignore
            
    except Exception as e:
        logger.error(f"Failed to check pending recordings: {e}", exc_info=True)
    finally:
        session.close()

@celery_app.task(bind=True)
def get_worker_device_status(self):
    """
    Check the worker's available processing device (CUDA/CPU).
    """
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else None
        return {
            "device": device,
            "gpu_name": gpu_name,
            "torch_version": torch.__version__
        }
    except ImportError:
        return {"device": "cpu", "error": "torch not installed"}
    except Exception as e:
        return {"device": "unknown", "error": str(e)}

@celery_app.task(bind=True)
def download_models_task(self, hf_token: str | None = None, whisper_model_size: str | None = None):
    """
    Task to download models in the background.
    Checks for existing downloads from preload_models.py and forwards that progress.
    """
    from backend.preload_models import download_models, check_model_status
    from backend.utils.download_progress import (
        get_download_progress,
        is_download_in_progress,
        is_download_complete,
        set_download_progress
    )
    
    # Reload config to ensure we have the latest settings
    config_manager.reload()
    
    # Check if models are already fully downloaded
    model_status = check_model_status(whisper_model_size=whisper_model_size or "turbo")
    all_downloaded = (
        model_status.get("whisper", {}).get("downloaded", False) and
        model_status.get("pyannote", {}).get("downloaded", False) and
        model_status.get("embedding", {}).get("downloaded", False)
    )
    
    if all_downloaded:
        logger.info("All models already downloaded, skipping download.")
        self.update_state(state='PROCESSING', meta={
            'progress': 100,
            'message': 'All models already downloaded!'
        })
        set_download_progress(100, "All models already downloaded!", status="complete")
        return {"status": "success", "message": "All models already downloaded."}
    
    # Check if there's an active download from preload_models.py
    # If so, poll and forward that progress until it completes
    if is_download_in_progress():
        logger.info("Download already in progress (from preload_models.py), forwarding progress...")
        while True:
            progress = get_download_progress()
            if progress is None:
                break
            
            status = progress.get("status", "downloading")
            if status == "complete":
                self.update_state(state='PROCESSING', meta={
                    'progress': 100,
                    'message': 'All models downloaded!'
                })
                return {"status": "success", "message": "All models downloaded successfully."}
            elif status == "error":
                error_msg = progress.get("message", "Download failed")
                raise Exception(error_msg)
            else:
                # Forward the progress to the Celery task state
                meta = {
                    'progress': progress.get('progress', 0),
                    'message': progress.get('message', 'Downloading...'),
                    'stage': progress.get('stage')
                }
                if progress.get('speed'):
                    meta['speed'] = progress['speed']
                if progress.get('eta'):
                    meta['eta'] = progress['eta']
                self.update_state(state='PROCESSING', meta=meta)
            
            time.sleep(1)
    
    # No active download, proceed with our own download
    def progress_callback(msg, percent, speed=None, eta=None, stage=None):
        meta = {'progress': percent, 'message': msg, 'stage': stage}
        if speed:
            meta['speed'] = speed
        if eta:
            meta['eta'] = eta
        self.update_state(state='PROCESSING', meta=meta)
    
    try:
        download_models(progress_callback=progress_callback, hf_token=hf_token, whisper_model_size=whisper_model_size)
        return {"status": "success", "message": "All models downloaded successfully."}
    except Exception as e:
        logger.error(f"Model download failed: {e}", exc_info=True)
        raise e

@celery_app.task(base=DatabaseTask, bind=True)
def generate_notes_task(self, recording_id: int):
    """
    Generate meeting notes for a recording.
    """
    session = self.session
    recording = None
    transcript = None
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found.")
            return

        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        if not transcript:
            logger.error(f"Transcript for recording {recording_id} not found.")
            return

        # Update status
        transcript.notes_status = "generating"
        transcript.error_message = None
        recording.processing_step = "Generating meeting notes..."
        recording.processing_progress = 97
        session.add(transcript)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)

        # Get User Settings
        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings

        llm_config = resolve_llm_config(session, user_settings)
        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            logger.warning("Cannot generate notes: %s", missing_llm_config)
            _mark_notes_generation_error(session, recording, transcript, missing_llm_config)
            return

        segments = build_transcript_segments_for_read(
            session,
            recording_id,
            transcript=transcript,
        )
        if not segments:
            _mark_notes_generation_error(session, recording, transcript, "Transcript is empty")
            return

        # Build Speaker Map and Transcript Text
        speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
        speaker_map = build_recording_speaker_map(speakers)
        transcript_text = format_segments_for_llm(segments, speaker_map)

        # Call LLM Service
        llm = _llm_backend_from_config(llm_config)
        notes = llm.generate_meeting_notes(
            transcript_text,
            speaker_map,
            timeout=300,
            user_notes=transcript.user_notes,
            meeting_context=_resolve_meeting_event_context(session, recording),
        )

        # Save Notes
        transcript.notes = notes
        transcript.notes_status = "completed"
        transcript.error_message = None
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        session.add(transcript)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)
        logger.info(f"Generated meeting notes for recording {recording_id}")

        # --- Index Notes for RAG ---
        try:
            # Clean up existing note chunks
            existing_chunks = session.exec(
                select(ContextChunk)
                .where(ContextChunk.recording_id == recording_id)
                .where(ContextChunk.document_id == None)
            ).all()
            
            for chunk in existing_chunks:
                if chunk.meta and chunk.meta.get('source') == 'notes':
                    session.delete(chunk)
            
            # Chunking
            from backend.processing.text_embedding import get_text_embedding_service
            
            note_chunks = []
            CHUNK_SIZE = 1000
            OVERLAP = 100
            
            if notes:
                start = 0
                while start < len(notes):
                    end = start + CHUNK_SIZE
                    note_chunks.append(notes[start:end])
                    start += (CHUNK_SIZE - OVERLAP)
            
            if note_chunks:
                embedding_service = get_text_embedding_service()
                vectors = embedding_service.embed(note_chunks)
                
                for i, (text_chunk, vector) in enumerate(zip(note_chunks, vectors)):
                    db_chunk = ContextChunk(
                        recording_id=recording_id,
                        content=text_chunk,
                        embedding=vector,
                        meta={"chunk_index": i, "source": "notes"}
                    )
                    session.add(db_chunk)
                session.commit()
                logger.info(f"Indexed {len(note_chunks)} note chunks for recording {recording_id}")

        except Exception as e:
            logger.error(f"Failed to index meeting notes for RAG: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Failed to generate meeting notes: {e}", exc_info=True)
        session.rollback()
        if transcript is None:
            transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        _mark_notes_generation_error(session, recording, transcript, e)

@celery_app.task(base=DatabaseTask, bind=True)
def infer_speakers_task(self, recording_id: int):
    """
    Independent task to re-run speaker inference using LLM.
    """
    # Reload config
    config_manager.reload()
    
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found.")
            return

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        segments = build_transcript_segments_for_read(
            session,
            recording_id,
            transcript=transcript,
        )
        if not transcript or not segments:
            logger.error(f"No transcript found for recording {recording_id}.")
            _complete_speaker_inference_task(session, recording)
            return

        speakers = session.exec(
            select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
        ).all()
        eligible_labels = get_speakers_eligible_for_llm_renaming(speakers)
        meeting_context = _resolve_meeting_event_context(session, recording)

        # Fetch user settings for provider resolution.
        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings
        llm_config = resolve_llm_config(session, user_settings)
        missing_llm_config = llm_config.missing_configuration_message()

        suggestion_count = 0
        rule_based_result = SpeakerInferenceResult()
        if missing_llm_config:
            rule_based_result = detect_rule_based_speaker_suggestions(
                segments,
                eligible_labels,
                meeting_context,
            )
            suggestion_count += _persist_generated_speaker_name_suggestions(
                session,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                inference_result=rule_based_result,
                origin="manual_retry",
                provider=None,
                replaced_reason="manual_retry_refresh",
            )
            logger.warning(
                "Cannot infer speakers for recording %s: %s",
                recording_id,
                missing_llm_config,
            )
            if suggestion_count:
                session.commit()
                record_pipeline_metric(
                    stage="speaker_name_suggestions_generated",
                    recording_id=recording_id,
                    payload={
                        "origin": "manual_retry",
                        "suggestion_count": suggestion_count,
                        "rule_based_count": len(rule_based_result.suggestions),
                        "llm_count": 0,
                    },
                    log=logger,
                )
            _complete_speaker_inference_task(session, recording)
            return

        # Update status (optional, but good for UI feedback if we had a specific status for this)
        # For now, we just log it.
        logger.info(f"Starting independent speaker inference for recording {recording_id}")

        llm_result = SpeakerInferenceResult()
        if eligible_labels:
            transcript_for_llm = ""
            for seg in segments:
                start = seg.get("start", 0)
                end = seg.get("end", 0)

                def fmt(ts):
                    h = int(ts // 3600)
                    m = int((ts % 3600) // 60)
                    s = ts % 60
                    return f"{h:02}.{m:02}.{s:05.2f}s"

                diarization_label = seg.get("speaker", "Unknown")
                text = seg.get("text", "")
                transcript_for_llm += (
                    f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {text}\n"
                )

            backend = _llm_backend_from_config(llm_config)
            llm_result = backend.infer_speaker_suggestions(
                transcript_for_llm,
                user_notes=transcript.user_notes,
                meeting_context=meeting_context,
                eligible_labels=eligible_labels,
            )
            suggestion_count += _persist_generated_speaker_name_suggestions(
                session,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                inference_result=llm_result,
                origin="manual_retry",
                provider=llm_config.provider,
                replaced_reason="manual_retry_refresh",
            )
            superseded_count = _supersede_pending_speaker_name_suggestions_for_labels(
                session,
                transcript=transcript,
                diarization_labels=(
                    label for label in eligible_labels if label not in llm_result.mapping
                ),
                reason="manual_retry_omitted_by_llm",
            )
        else:
            superseded_count = 0

        session.commit()
        record_pipeline_metric(
            stage="speaker_name_suggestions_generated",
            recording_id=recording_id,
            payload={
                "origin": "manual_retry",
                "suggestion_count": suggestion_count,
                "superseded_count": superseded_count,
                "rule_based_count": len(rule_based_result.suggestions),
                "llm_count": len(llm_result.suggestions),
            },
            log=logger,
        )
        logger.info(
            "Stored %s speaker suggestions for recording %s",
            suggestion_count,
            recording_id,
        )

        _complete_speaker_inference_task(session, recording)

    except Exception as e:
        logger.error(f"Speaker inference task failed: {e}", exc_info=True)
        # Revert status to PROCESSED on error so spinner stops
        try:
            recording = session.get(Recording, recording_id)
            _complete_speaker_inference_task(session, recording)
        except Exception as db_err:
            logger.error(f"Failed to revert recording status: {db_err}")

@celery_app.task(base=DatabaseTask, bind=True)
def cleanup_temp_recordings(self):
    """
    Periodic task to clean up old temporary files and failed uploads.
    Runs every 24 hours.
    """
    logger.info("Starting cleanup of temp recordings...")

    cleaned_count = cleanup_recording_audio_chunks(self.session, logger=logger)
    cleaned_count += cleanup_stale_recording_artifacts(max_age_hours=24, logger=logger)

    logger.info(f"Cleanup complete. Removed {cleaned_count} items.")

@celery_app.task(base=DatabaseTask, bind=True)
def generate_proxy_task(self, recording_id: int):
    """
    Generate a lightweight MP3 proxy file for frontend playback.
    """
    from backend.utils.audio import convert_to_proxy_mp3
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found for proxy generation")
            return

        if not recording.audio_path or not os.path.exists(recording.audio_path):
            logger.error(f"Audio file not found for recording {recording_id}")
            return

        # Define proxy path (same dir, .mp3 extension)
        base_path, _ = os.path.splitext(recording.audio_path)
        proxy_path = f"{base_path}.mp3"

        if _paths_point_to_same_media(recording.audio_path, proxy_path):
            logger.info(
                "Recording %s already uses an MP3 source; reusing it as proxy audio.",
                recording_id,
            )
            recording.proxy_path = recording.audio_path
            session.add(recording)
            session.commit()
            return

        logger.info(f"Generating proxy for recording {recording_id} at {proxy_path}")
        
        if convert_to_proxy_mp3(recording.audio_path, proxy_path):
            recording.proxy_path = proxy_path
            session.add(recording)
            session.commit()
            logger.info(f"Proxy generated successfully for recording {recording_id}")
            
            # If processing is already finished, delete source audio
            if recording.status in [RecordingStatus.PROCESSED, RecordingStatus.ERROR] and _can_delete_source_audio(recording):
                try:
                    logger.info(f"Storage optimization: Proxy generated after processing, deleting source audio {recording.audio_path}")
                    os.remove(recording.audio_path)
                except Exception as e:
                    logger.error(f"Failed to delete source audio {recording.audio_path}: {e}")
        else:
            logger.error(f"Failed to generate proxy for recording {recording_id}")

    except Exception as e:
        logger.error(f"Error in generate_proxy_task for recording {recording_id}: {e}")
        # Not re-raised because proxy generation is optional/secondary.

@celery_app.task(base=DatabaseTask, bind=True)
def process_document_task(self, document_id: int):
    """
    Process an uploaded document: chunk text, embed, and store context chunks.
    """
    session = self.session
    document = session.get(Document, document_id)
    if not document:
        logger.error(f"Document {document_id} not found.")
        return

    try:
        document.status = DocumentStatus.PROCESSING
        session.add(document)
        session.commit()

        # Read file content
        content = ""
        if document.file_path.endswith(".txt") or document.file_path.endswith(".md"):
            with open(document.file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        elif document.file_path.endswith(".pdf"):
            import fitz # PyMuPDF
            try:
                doc = fitz.open(document.file_path)
                for page in doc:
                    content += page.get_text() + "\n\n"
            except Exception as e:
                logger.error(f"Failed to extract text from PDF {document.file_path}: {e}")
                raise Exception(f"PDF extraction failed: {str(e)}")
        
        if not content:
            logger.warning(f"File content empty or unsupported type: {document.file_path}")
            pass

        # Chunking Strategy (Simple overlapping sliding window)
        CHUNK_SIZE = 500 # characters
        OVERLAP = 50
        
        chunks = []
        if content:
            start = 0
            while start < len(content):
                end = start + CHUNK_SIZE
                chunk_text = content[start:end]
                chunks.append(chunk_text)
                start += (CHUNK_SIZE - OVERLAP)
        
        if not chunks:
             logger.warning(f"No chunks generated for document {document_id}")
             document.status = DocumentStatus.READY
             session.add(document)
             session.commit()
             return

        # Embed chunks
        embedding_service = get_text_embedding_service()
        vectors = embedding_service.embed(chunks)
        
        # Store Chunks
        for i, (text_chunk, vector) in enumerate(zip(chunks, vectors)):
            db_chunk = ContextChunk(
                recording_id=document.recording_id,
                document_id=document.id,
                content=text_chunk,
                embedding=vector,
                meta={"chunk_index": i, "source": "document"}
            )
            session.add(db_chunk)
        
        document.status = DocumentStatus.READY
        session.add(document)
        session.commit()
        logger.info(f"Processed document {document_id}: {len(chunks)} chunks created.")

    except Exception as e:
        logger.error(f"Failed to process document {document_id}: {e}", exc_info=True)
        document.status = DocumentStatus.ERROR
        document.error_message = str(e)
        session.add(document)
        session.commit()

@celery_app.task(bind=True)
def sync_calendar_connection_task(self, connection_id: int):
    """
    Refresh a single connected calendar account.
    """
    import asyncio

    from backend.services.calendar_service import sync_connection_by_id

    asyncio.run(sync_connection_by_id(connection_id))
    return {"status": "success", "connection_id": connection_id}

@celery_app.task(bind=True)
def sync_calendar_connections_task(self):
    """
    Periodic sync for all selected calendar connections.
    """
    import asyncio

    from backend.services.calendar_service import sync_all_connections

    synced_connections = asyncio.run(sync_all_connections())
    return {"status": "success", "connections_synced": synced_connections}

@celery_app.task(base=DatabaseTask, bind=True)
def index_transcript_task(self, recording_id: int):
    """
    Index the transcript of a completed recording for RAG.
    """
    session = self.session
    recording = session.get(Recording, recording_id)
    if not recording:
        return

    transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    segments = build_transcript_segments_for_read(
        session,
        recording_id,
        transcript=transcript,
    )
    if not transcript or not segments:
        return

    try:
        # Clear existing transcript chunks for this recording
        # The 'source' metadata field identifies these chunks.
        
        # Selects then deletes context chunks.
        existing_chunks = session.exec(
            select(ContextChunk)
            .where(ContextChunk.recording_id == recording_id)
            .where(ContextChunk.document_id == None).where(ContextChunk.meta['source'].as_string() == '"transcript"') 
            # Using document_id == None serves as a proxy for non-document chunks.
        ).all()
        
        for chunk in existing_chunks:
            if chunk.meta.get('source') == 'transcript':
                session.delete(chunk)
        
        # Chunks the transcript segments.
        # Grouping small segments improves embedding quality.
        
        segments = [dict(segment) for segment in segments]
        
        temp_chunk_text = ""
        temp_chunk_start = 0
        temp_chunk_end = 0
        temp_meta_speakers = set()
        
        chunks_to_embed = []
        metas = []
        
        current_length = 0
        TARGET_LENGTH = 1000 # chars
        
        for seg in segments:
            text = seg['text']
            start = seg['start']
            end = seg['end']
            speaker = seg['speaker']
            
            if current_length == 0:
                temp_chunk_start = start
            
            temp_chunk_text += f"{speaker}: {text}\n"
            current_length += len(text)
            temp_meta_speakers.add(speaker)
            temp_chunk_end = end
            
            if current_length >= TARGET_LENGTH:
                chunks_to_embed.append(temp_chunk_text)
                metas.append({
                    "start": temp_chunk_start,
                    "end": temp_chunk_end,
                    "speakers": list(temp_meta_speakers),
                    "source": "transcript"
                })
                
                # Reset
                temp_chunk_text = ""
                current_length = 0
                temp_meta_speakers = set()
                
        # Add remaining
        if temp_chunk_text:
             chunks_to_embed.append(temp_chunk_text)
             metas.append({
                "start": temp_chunk_start,
                "end": temp_chunk_end,
                "speakers": list(temp_meta_speakers),
                "source": "transcript"
            })
            
        if not chunks_to_embed:
            return

        embedding_service = get_text_embedding_service()
        vectors = embedding_service.embed(chunks_to_embed)
        
        for text, meta, vector in zip(chunks_to_embed, metas, vectors):
            db_chunk = ContextChunk(
                recording_id=recording_id,
                content=text,
                embedding=vector,
                meta=meta
            )
            session.add(db_chunk)
            
        session.commit()
        logger.info(f"Indexed transcript for recording {recording_id}: {len(chunks_to_embed)} chunks.")

    except Exception as e:
        logger.error(f"Failed to index transcript {recording_id}: {e}", exc_info=True)


@celery_app.task(bind=True)
def create_backup_task(self, include_audio: bool = True):
    """
    Background task to create a backup zip file.
    Returns the path to the backup file.
    """
    from backend.core.backup_manager import BackupManager
    
    try:
        logger.info(f"Starting backup task (include_audio={include_audio})")
        self.update_state(state='PROCESSING', meta={'status': 'Creating backup...'})
        
        zip_path = BackupManager.create_backup_blocking(include_audio=include_audio)
        
        logger.info(f"Backup created successfully at {zip_path}")
        return {"status": "success", "zip_path": zip_path}
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise e
