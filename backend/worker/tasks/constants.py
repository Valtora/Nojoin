import os
import shutil
import logging
import hashlib
import time
from datetime import datetime, timedelta
import warnings
import urllib.error
import requests.exceptions

from typing import TYPE_CHECKING, Any, Iterable, Sequence
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
    refresh_transcript_projection_from_canonical,
    refine_recording_utterances_via_segmentation,
)
from backend.utils.rolling_diarization import (
    build_diarization_window_payload,
    build_rolling_diarization_config_hash,
    get_rolling_diarization_model_name,
    persist_diarization_window_result,
)

if TYPE_CHECKING:
    from backend.processing.embedding import cosine_similarity, merge_embeddings
    from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
    from backend.utils.audio import get_audio_duration, convert_to_mp3, convert_to_proxy_mp3
    from backend.processing.llm_services import get_llm_backend_with_secondary
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
MEETING_EDGE_MIN_REFRESH_SECONDS = 60
MEETING_EDGE_MIN_NEW_SEGMENTS = 3
MEETING_EDGE_MIN_NEW_WORDS = 60
MEETING_EDGE_RECENT_SEGMENTS = 20
MEETING_EDGE_MAX_TRANSCRIPT_CHARS = 12000
MEETING_EDGE_STATUS_IDLE = "idle"
MEETING_EDGE_STATUS_UPDATING = "updating"
MEETING_EDGE_STATUS_READY = "ready"
MEETING_EDGE_STATUS_ERROR = "error"



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



def _to_optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None



# --- IMPORTED REDIRECTION WRAPPERS FOR TEST MONKEYPATCHING ---

_auto_link_recording_orig = auto_link_recording
def auto_link_recording(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'auto_link_recording'):
        actual = tasks.auto_link_recording
        if actual is not auto_link_recording and getattr(actual, '__code__', None) is not auto_link_recording.__code__:
            return actual(*args, **kwargs)
    return _auto_link_recording_orig(*args, **kwargs)

_build_recording_speaker_map_orig = build_recording_speaker_map
def build_recording_speaker_map(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'build_recording_speaker_map'):
        actual = tasks.build_recording_speaker_map
        if actual is not build_recording_speaker_map and getattr(actual, '__code__', None) is not build_recording_speaker_map.__code__:
            return actual(*args, **kwargs)
    return _build_recording_speaker_map_orig(*args, **kwargs)

_build_reusable_live_segments_orig = build_reusable_live_segments
def build_reusable_live_segments(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'build_reusable_live_segments'):
        actual = tasks.build_reusable_live_segments
        if actual is not build_reusable_live_segments and getattr(actual, '__code__', None) is not build_reusable_live_segments.__code__:
            return actual(*args, **kwargs)
    return _build_reusable_live_segments_orig(*args, **kwargs)

_build_transcript_segments_for_read_orig = build_transcript_segments_for_read
def build_transcript_segments_for_read(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'build_transcript_segments_for_read'):
        actual = tasks.build_transcript_segments_for_read
        if actual is not build_transcript_segments_for_read and getattr(actual, '__code__', None) is not build_transcript_segments_for_read.__code__:
            return actual(*args, **kwargs)
    return _build_transcript_segments_for_read_orig(*args, **kwargs)

_collect_pending_chunk_spans_orig = collect_pending_chunk_spans
def collect_pending_chunk_spans(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'collect_pending_chunk_spans'):
        actual = tasks.collect_pending_chunk_spans
        if actual is not collect_pending_chunk_spans and getattr(actual, '__code__', None) is not collect_pending_chunk_spans.__code__:
            return actual(*args, **kwargs)
    return _collect_pending_chunk_spans_orig(*args, **kwargs)

_complete_recording_asr_window_result_orig = complete_recording_asr_window_result
def complete_recording_asr_window_result(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'complete_recording_asr_window_result'):
        actual = tasks.complete_recording_asr_window_result
        if actual is not complete_recording_asr_window_result and getattr(actual, '__code__', None) is not complete_recording_asr_window_result.__code__:
            return actual(*args, **kwargs)
    return _complete_recording_asr_window_result_orig(*args, **kwargs)

_ensure_processing_run_orig = ensure_processing_run
def ensure_processing_run(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'ensure_processing_run'):
        actual = tasks.ensure_processing_run
        if actual is not ensure_processing_run and getattr(actual, '__code__', None) is not ensure_processing_run.__code__:
            return actual(*args, **kwargs)
    return _ensure_processing_run_orig(*args, **kwargs)

_fail_recording_asr_window_result_orig = fail_recording_asr_window_result
def fail_recording_asr_window_result(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'fail_recording_asr_window_result'):
        actual = tasks.fail_recording_asr_window_result
        if actual is not fail_recording_asr_window_result and getattr(actual, '__code__', None) is not fail_recording_asr_window_result.__code__:
            return actual(*args, **kwargs)
    return _fail_recording_asr_window_result_orig(*args, **kwargs)

_finalize_utterances_from_segments_orig = finalize_utterances_from_segments
def finalize_utterances_from_segments(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'finalize_utterances_from_segments'):
        actual = tasks.finalize_utterances_from_segments
        if actual is not finalize_utterances_from_segments and getattr(actual, '__code__', None) is not finalize_utterances_from_segments.__code__:
            return actual(*args, **kwargs)
    return _finalize_utterances_from_segments_orig(*args, **kwargs)

_flag_modified_orig = flag_modified
def flag_modified(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'flag_modified'):
        actual = tasks.flag_modified
        if actual is not flag_modified and getattr(actual, '__code__', None) is not flag_modified.__code__:
            return actual(*args, **kwargs)
    return _flag_modified_orig(*args, **kwargs)

_get_recording_asr_window_result_orig = get_recording_asr_window_result
def get_recording_asr_window_result(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'get_recording_asr_window_result'):
        actual = tasks.get_recording_asr_window_result
        if actual is not get_recording_asr_window_result and getattr(actual, '__code__', None) is not get_recording_asr_window_result.__code__:
            return actual(*args, **kwargs)
    return _get_recording_asr_window_result_orig(*args, **kwargs)

_get_speakers_eligible_for_llm_renaming_orig = get_speakers_eligible_for_llm_renaming
def get_speakers_eligible_for_llm_renaming(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'get_speakers_eligible_for_llm_renaming'):
        actual = tasks.get_speakers_eligible_for_llm_renaming
        if actual is not get_speakers_eligible_for_llm_renaming and getattr(actual, '__code__', None) is not get_speakers_eligible_for_llm_renaming.__code__:
            return actual(*args, **kwargs)
    return _get_speakers_eligible_for_llm_renaming_orig(*args, **kwargs)

_get_sync_session_orig = get_sync_session
def get_sync_session(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'get_sync_session'):
        actual = tasks.get_sync_session
        if actual is not get_sync_session and getattr(actual, '__code__', None) is not get_sync_session.__code__:
            return actual(*args, **kwargs)
    return _get_sync_session_orig(*args, **kwargs)

_mark_recording_audio_chunks_ready_for_cleanup_orig = mark_recording_audio_chunks_ready_for_cleanup
def mark_recording_audio_chunks_ready_for_cleanup(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'mark_recording_audio_chunks_ready_for_cleanup'):
        actual = tasks.mark_recording_audio_chunks_ready_for_cleanup
        if actual is not mark_recording_audio_chunks_ready_for_cleanup and getattr(actual, '__code__', None) is not mark_recording_audio_chunks_ready_for_cleanup.__code__:
            return actual(*args, **kwargs)
    return _mark_recording_audio_chunks_ready_for_cleanup_orig(*args, **kwargs)

_reconcile_completed_diarization_windows_orig = reconcile_completed_diarization_windows
def reconcile_completed_diarization_windows(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'reconcile_completed_diarization_windows'):
        actual = tasks.reconcile_completed_diarization_windows
        if actual is not reconcile_completed_diarization_windows and getattr(actual, '__code__', None) is not reconcile_completed_diarization_windows.__code__:
            return actual(*args, **kwargs)
    return _reconcile_completed_diarization_windows_orig(*args, **kwargs)

_refine_recording_utterances_via_segmentation_orig = refine_recording_utterances_via_segmentation
def refine_recording_utterances_via_segmentation(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'refine_recording_utterances_via_segmentation'):
        actual = tasks.refine_recording_utterances_via_segmentation
        if actual is not refine_recording_utterances_via_segmentation and getattr(actual, '__code__', None) is not refine_recording_utterances_via_segmentation.__code__:
            return actual(*args, **kwargs)
    return _refine_recording_utterances_via_segmentation_orig(*args, **kwargs)

_refresh_transcript_projection_from_canonical_orig = refresh_transcript_projection_from_canonical
def refresh_transcript_projection_from_canonical(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'refresh_transcript_projection_from_canonical'):
        actual = tasks.refresh_transcript_projection_from_canonical
        if actual is not refresh_transcript_projection_from_canonical and getattr(actual, '__code__', None) is not refresh_transcript_projection_from_canonical.__code__:
            return actual(*args, **kwargs)
    return _refresh_transcript_projection_from_canonical_orig(*args, **kwargs)

_resolve_llm_config_orig = resolve_llm_config
def resolve_llm_config(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'resolve_llm_config'):
        actual = tasks.resolve_llm_config
        if actual is not resolve_llm_config and getattr(actual, '__code__', None) is not resolve_llm_config.__code__:
            return actual(*args, **kwargs)
    return _resolve_llm_config_orig(*args, **kwargs)

_serialize_meeting_edge_result_orig = serialize_meeting_edge_result
def serialize_meeting_edge_result(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'serialize_meeting_edge_result'):
        actual = tasks.serialize_meeting_edge_result
        if actual is not serialize_meeting_edge_result and getattr(actual, '__code__', None) is not serialize_meeting_edge_result.__code__:
            return actual(*args, **kwargs)
    return _serialize_meeting_edge_result_orig(*args, **kwargs)

_start_recording_asr_window_result_orig = start_recording_asr_window_result
def start_recording_asr_window_result(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'start_recording_asr_window_result'):
        actual = tasks.start_recording_asr_window_result
        if actual is not start_recording_asr_window_result and getattr(actual, '__code__', None) is not start_recording_asr_window_result.__code__:
            return actual(*args, **kwargs)
    return _start_recording_asr_window_result_orig(*args, **kwargs)

_update_recording_status_orig = update_recording_status
def update_recording_status(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks and hasattr(tasks, 'update_recording_status'):
        actual = tasks.update_recording_status
        if actual is not update_recording_status and getattr(actual, '__code__', None) is not update_recording_status.__code__:
            return actual(*args, **kwargs)
    return _update_recording_status_orig(*args, **kwargs)

# --- DEFINED REDIRECTION WRAPPERS FOR TEST MONKEYPATCHING AND CROSS-SUBMODULE CALLS ---

def _build_automatic_meeting_intelligence_transcript(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_build_automatic_meeting_intelligence_transcript', None)
        if actual and getattr(actual, '__code__', None) is not _build_automatic_meeting_intelligence_transcript.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_build_automatic_meeting_intelligence_transcript_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_build_automatic_meeting_intelligence_transcript_impl not found in tasks module")

def _build_catch_up_segments(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_build_catch_up_segments', None)
        if actual and getattr(actual, '__code__', None) is not _build_catch_up_segments.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_build_catch_up_segments_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_build_catch_up_segments_impl not found in tasks module")

def _build_final_diarization_plan(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_build_final_diarization_plan', None)
        if actual and getattr(actual, '__code__', None) is not _build_final_diarization_plan.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_build_final_diarization_plan_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_build_final_diarization_plan_impl not found in tasks module")

def _has_meeting_edge_signal(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_has_meeting_edge_signal', None)
        if actual and getattr(actual, '__code__', None) is not _has_meeting_edge_signal.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_has_meeting_edge_signal_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_has_meeting_edge_signal_impl not found in tasks module")

def _llm_backend_from_config(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_llm_backend_from_config', None)
        if actual and getattr(actual, '__code__', None) is not _llm_backend_from_config.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_llm_backend_from_config_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_llm_backend_from_config_impl not found in tasks module")

def _load_recording_audio_chunks(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_load_recording_audio_chunks', None)
        if actual and getattr(actual, '__code__', None) is not _load_recording_audio_chunks.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_load_recording_audio_chunks_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_load_recording_audio_chunks_impl not found in tasks module")

def _load_recording_audio_window_manifests(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_load_recording_audio_window_manifests', None)
        if actual and getattr(actual, '__code__', None) is not _load_recording_audio_window_manifests.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_load_recording_audio_window_manifests_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_load_recording_audio_window_manifests_impl not found in tasks module")

def _recording_has_completed_diarization_windows(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_recording_has_completed_diarization_windows', None)
        if actual and getattr(actual, '__code__', None) is not _recording_has_completed_diarization_windows.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_recording_has_completed_diarization_windows_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_recording_has_completed_diarization_windows_impl not found in tasks module")

def _recording_uses_browser_capture(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_recording_uses_browser_capture', None)
        if actual and getattr(actual, '__code__', None) is not _recording_uses_browser_capture.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_recording_uses_browser_capture_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_recording_uses_browser_capture_impl not found in tasks module")

def _resolve_meeting_event_context(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_resolve_meeting_event_context', None)
        if actual and getattr(actual, '__code__', None) is not _resolve_meeting_event_context.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_resolve_meeting_event_context_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_resolve_meeting_event_context_impl not found in tasks module")

def _run_automatic_meeting_intelligence_stage(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_run_automatic_meeting_intelligence_stage', None)
        if actual and getattr(actual, '__code__', None) is not _run_automatic_meeting_intelligence_stage.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_run_automatic_meeting_intelligence_stage_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_run_automatic_meeting_intelligence_stage_impl not found in tasks module")

def _run_catch_up_diarization_windows(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_run_catch_up_diarization_windows', None)
        if actual and getattr(actual, '__code__', None) is not _run_catch_up_diarization_windows.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_run_catch_up_diarization_windows_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_run_catch_up_diarization_windows_impl not found in tasks module")

def _should_refresh_meeting_edge(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_should_refresh_meeting_edge', None)
        if actual and getattr(actual, '__code__', None) is not _should_refresh_meeting_edge.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_should_refresh_meeting_edge_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_should_refresh_meeting_edge_impl not found in tasks module")

def _summarize_completed_diarization_window_speaker_evidence(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_summarize_completed_diarization_window_speaker_evidence', None)
        if actual and getattr(actual, '__code__', None) is not _summarize_completed_diarization_window_speaker_evidence.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_summarize_completed_diarization_window_speaker_evidence_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_summarize_completed_diarization_window_speaker_evidence_impl not found in tasks module")

def _paths_point_to_same_media(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_paths_point_to_same_media', None)
        if actual and getattr(actual, '__code__', None) is not _paths_point_to_same_media.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_paths_point_to_same_media_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_paths_point_to_same_media_impl not found in tasks module")

def _persist_catch_up_diarization_window(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_persist_catch_up_diarization_window', None)
        if actual and getattr(actual, '__code__', None) is not _persist_catch_up_diarization_window.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_persist_catch_up_diarization_window_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_persist_catch_up_diarization_window_impl not found in tasks module")

def _mark_notes_generation_error(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_mark_notes_generation_error', None)
        if actual and getattr(actual, '__code__', None) is not _mark_notes_generation_error.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_mark_notes_generation_error_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_mark_notes_generation_error_impl not found in tasks module")

def _persist_generated_speaker_name_suggestions(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_persist_generated_speaker_name_suggestions', None)
        if actual and getattr(actual, '__code__', None) is not _persist_generated_speaker_name_suggestions.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_persist_generated_speaker_name_suggestions_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_persist_generated_speaker_name_suggestions_impl not found in tasks module")

def _supersede_pending_speaker_name_suggestions_for_labels(*args, **kwargs):
    import sys
    tasks = sys.modules.get('backend.worker.tasks')
    if tasks:
        actual = getattr(tasks, '_supersede_pending_speaker_name_suggestions_for_labels', None)
        if actual and getattr(actual, '__code__', None) is not _supersede_pending_speaker_name_suggestions_for_labels.__code__:
            return actual(*args, **kwargs)
        impl = getattr(tasks, '_supersede_pending_speaker_name_suggestions_for_labels_impl', None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_supersede_pending_speaker_name_suggestions_for_labels_impl not found in tasks module")


__all__ = [name for name in globals() if not name.startswith('__')]
