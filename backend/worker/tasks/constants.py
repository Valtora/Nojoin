import hashlib
import logging
import os
import shutil
import time
import urllib.error
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import requests.exceptions
from celery import Task
from celery.signals import worker_ready
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.core.exceptions import (
    AudioFormatError,
    AudioProcessingError,
    VADNoSpeechError,
)
from backend.core.single_flight import single_flight
from backend.models.calendar import CalendarEvent
from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk

# Heavy processing imports stay inside the tasks so the API never loads torch.
from backend.models.document import Document, DocumentStatus
from backend.models.invitation import Invitation
from backend.models.pipeline import (
    RecordingAudioChunk,
    TranscriptUtteranceState,
)
from backend.models.recording import (
    ClientStatus,
    Recording,
    RecordingStatus,
    recording_supports_unified_mutations,
)
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import RecordingTag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.pipeline_metrics import (
    pipeline_metric_timer,
    record_pipeline_metric,
)
from backend.services.calendar_link_service import auto_link_recording
from backend.services.timeline_note_service import load_recording_timeline_notes
from backend.utils.canonical_pipeline import (
    apply_recording_speaker_identity_fields,
    build_transcript_segments_for_read,
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
)
from backend.utils.config_manager import (
    MEETING_EDGE_CONTEXT_LEVEL_MAX,
    config_manager,
    get_meeting_edge_context_level,
    is_meeting_edge_enabled,
)
from backend.utils.languages import (
    apply_recording_language,
    resolve_language_preferences,
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
    append_user_notes_section,
    build_recording_speaker_map,
    format_segments_for_llm,
    meeting_event_context_from_calendar_event,
)
from backend.utils.notes_templates import (
    ResolvedNotesTemplate,
    build_notes_prompt_context,
    resolve_glossary,
)
from backend.utils.recording_storage import (
    cleanup_orphaned_uploading_recordings,
    cleanup_recording_audio_chunks,
    cleanup_stale_recording_artifacts,
    mark_recording_audio_chunks_ready_for_cleanup,
)
from backend.utils.speaker_name_suggestions import (
    SPEAKER_SUGGESTION_STATUS_ACCEPTED,
    SpeakerInferenceResult,
    build_mapping_based_speaker_suggestions,
    build_persisted_speaker_suggestion,
    detect_rule_based_speaker_suggestions,
    persist_transcript_speaker_suggestions,
    supersede_pending_transcript_speaker_suggestions,
)
from backend.utils.status_manager import update_recording_status
from backend.utils.stored_paths import confined_document_path, confined_recording_path
from backend.utils.time import utc_now
from backend.utils.timeline_notes import render_timeline_for_llm

if TYPE_CHECKING:
    import torch

    from backend.processing.embedding import cosine_similarity, merge_embeddings
    from backend.processing.llm_services import get_llm_backend_with_secondary
    from backend.utils.audio import (
        convert_to_mp3,
        convert_to_proxy_mp3,
        get_audio_duration,
    )
    from backend.utils.transcript_utils import (
        combine_transcription_diarization,
        consolidate_diarized_transcript,
    )

logger = logging.getLogger(__name__)


# Suppress specific warnings in the worker process
warnings.filterwarnings("ignore", message=r".*std\(\): degrees of freedom is <= 0.*")

AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS = 300
AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS = 97
AUTOMATIC_MEETING_INTELLIGENCE_STAGE = "Generating Notes"
AUTOMATIC_MEETING_INTELLIGENCE_STEP = "Generating meeting notes..."

MEETING_EDGE_TIMEOUT_SECONDS = 90
# Comfortably above the timeout above, because this only has to bound how long a
# worker killed mid-refresh can block the next one. Tuning it near the typical
# run time would trade a rare wedge for a common double-run.
MEETING_EDGE_SINGLE_FLIGHT_TTL_SECONDS = MEETING_EDGE_TIMEOUT_SECONDS + 30
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


# --- IMPORTED REDIRECTION WRAPPERS FOR TEST MONKEYPATCHING ---

_auto_link_recording_orig = auto_link_recording


def auto_link_recording(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "auto_link_recording"):
        actual = tasks.auto_link_recording
        if (
            actual is not auto_link_recording
            and getattr(actual, "__code__", None) is not auto_link_recording.__code__
        ):
            return actual(*args, **kwargs)
    return _auto_link_recording_orig(*args, **kwargs)


_build_recording_speaker_map_orig = build_recording_speaker_map


def build_recording_speaker_map(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "build_recording_speaker_map"):
        actual = tasks.build_recording_speaker_map
        if (
            actual is not build_recording_speaker_map
            and getattr(actual, "__code__", None)
            is not build_recording_speaker_map.__code__
        ):
            return actual(*args, **kwargs)
    return _build_recording_speaker_map_orig(*args, **kwargs)


_build_transcript_segments_for_read_orig = build_transcript_segments_for_read


def build_transcript_segments_for_read(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "build_transcript_segments_for_read"):
        actual = tasks.build_transcript_segments_for_read
        if (
            actual is not build_transcript_segments_for_read
            and getattr(actual, "__code__", None)
            is not build_transcript_segments_for_read.__code__
        ):
            return actual(*args, **kwargs)
    return _build_transcript_segments_for_read_orig(*args, **kwargs)


_finalize_utterances_from_segments_orig = finalize_utterances_from_segments


def finalize_utterances_from_segments(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "finalize_utterances_from_segments"):
        actual = tasks.finalize_utterances_from_segments
        if (
            actual is not finalize_utterances_from_segments
            and getattr(actual, "__code__", None)
            is not finalize_utterances_from_segments.__code__
        ):
            return actual(*args, **kwargs)
    return _finalize_utterances_from_segments_orig(*args, **kwargs)


_flag_modified_orig = flag_modified


def flag_modified(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "flag_modified"):
        actual = tasks.flag_modified
        if (
            actual is not flag_modified
            and getattr(actual, "__code__", None) is not flag_modified.__code__
        ):
            return actual(*args, **kwargs)
    return _flag_modified_orig(*args, **kwargs)


_get_speakers_eligible_for_llm_renaming_orig = get_speakers_eligible_for_llm_renaming


def get_speakers_eligible_for_llm_renaming(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "get_speakers_eligible_for_llm_renaming"):
        actual = tasks.get_speakers_eligible_for_llm_renaming
        if (
            actual is not get_speakers_eligible_for_llm_renaming
            and getattr(actual, "__code__", None)
            is not get_speakers_eligible_for_llm_renaming.__code__
        ):
            return actual(*args, **kwargs)
    return _get_speakers_eligible_for_llm_renaming_orig(*args, **kwargs)


_get_sync_session_orig = get_sync_session


def get_sync_session(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "get_sync_session"):
        actual = tasks.get_sync_session
        if (
            actual is not get_sync_session
            and getattr(actual, "__code__", None) is not get_sync_session.__code__
        ):
            return actual(*args, **kwargs)
    return _get_sync_session_orig(*args, **kwargs)


_mark_recording_audio_chunks_ready_for_cleanup_orig = (
    mark_recording_audio_chunks_ready_for_cleanup
)


def mark_recording_audio_chunks_ready_for_cleanup(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "mark_recording_audio_chunks_ready_for_cleanup"):
        actual = tasks.mark_recording_audio_chunks_ready_for_cleanup
        if (
            actual is not mark_recording_audio_chunks_ready_for_cleanup
            and getattr(actual, "__code__", None)
            is not mark_recording_audio_chunks_ready_for_cleanup.__code__
        ):
            return actual(*args, **kwargs)
    return _mark_recording_audio_chunks_ready_for_cleanup_orig(*args, **kwargs)


_refresh_transcript_projection_from_canonical_orig = (
    refresh_transcript_projection_from_canonical
)


def refresh_transcript_projection_from_canonical(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "refresh_transcript_projection_from_canonical"):
        actual = tasks.refresh_transcript_projection_from_canonical
        if (
            actual is not refresh_transcript_projection_from_canonical
            and getattr(actual, "__code__", None)
            is not refresh_transcript_projection_from_canonical.__code__
        ):
            return actual(*args, **kwargs)
    return _refresh_transcript_projection_from_canonical_orig(*args, **kwargs)


_resolve_llm_config_orig = resolve_llm_config


def resolve_llm_config(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "resolve_llm_config"):
        actual = tasks.resolve_llm_config
        if (
            actual is not resolve_llm_config
            and getattr(actual, "__code__", None) is not resolve_llm_config.__code__
        ):
            return actual(*args, **kwargs)
    return _resolve_llm_config_orig(*args, **kwargs)


_serialize_meeting_edge_result_orig = serialize_meeting_edge_result


def serialize_meeting_edge_result(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "serialize_meeting_edge_result"):
        actual = tasks.serialize_meeting_edge_result
        if (
            actual is not serialize_meeting_edge_result
            and getattr(actual, "__code__", None)
            is not serialize_meeting_edge_result.__code__
        ):
            return actual(*args, **kwargs)
    return _serialize_meeting_edge_result_orig(*args, **kwargs)


_update_recording_status_orig = update_recording_status


def update_recording_status(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks and hasattr(tasks, "update_recording_status"):
        actual = tasks.update_recording_status
        if (
            actual is not update_recording_status
            and getattr(actual, "__code__", None)
            is not update_recording_status.__code__
        ):
            return actual(*args, **kwargs)
    return _update_recording_status_orig(*args, **kwargs)


# --- DEFINED REDIRECTION WRAPPERS FOR TEST MONKEYPATCHING AND CROSS-SUBMODULE CALLS ---


def _build_automatic_meeting_intelligence_transcript(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(
            tasks, "_build_automatic_meeting_intelligence_transcript", None
        )
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _build_automatic_meeting_intelligence_transcript.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(
            tasks, "_build_automatic_meeting_intelligence_transcript_impl", None
        )
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError(
        "_build_automatic_meeting_intelligence_transcript_impl not found in tasks module"
    )


def _has_meeting_edge_signal(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_has_meeting_edge_signal", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _has_meeting_edge_signal.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_has_meeting_edge_signal_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_has_meeting_edge_signal_impl not found in tasks module")


def _llm_backend_from_config(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_llm_backend_from_config", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _llm_backend_from_config.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_llm_backend_from_config_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_llm_backend_from_config_impl not found in tasks module")


def _recording_uses_browser_capture(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_recording_uses_browser_capture", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _recording_uses_browser_capture.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_recording_uses_browser_capture_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_recording_uses_browser_capture_impl not found in tasks module")


def _resolve_meeting_event_context(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_resolve_meeting_event_context", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _resolve_meeting_event_context.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_resolve_meeting_event_context_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_resolve_meeting_event_context_impl not found in tasks module")


def _run_automatic_meeting_intelligence_stage(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_run_automatic_meeting_intelligence_stage", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _run_automatic_meeting_intelligence_stage.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_run_automatic_meeting_intelligence_stage_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError(
        "_run_automatic_meeting_intelligence_stage_impl not found in tasks module"
    )


def _should_refresh_meeting_edge(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_should_refresh_meeting_edge", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _should_refresh_meeting_edge.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_should_refresh_meeting_edge_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_should_refresh_meeting_edge_impl not found in tasks module")


def _paths_point_to_same_media(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_paths_point_to_same_media", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _paths_point_to_same_media.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_paths_point_to_same_media_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_paths_point_to_same_media_impl not found in tasks module")


def _mark_notes_generation_error(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_mark_notes_generation_error", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _mark_notes_generation_error.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_mark_notes_generation_error_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError("_mark_notes_generation_error_impl not found in tasks module")


def _persist_generated_speaker_name_suggestions(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(tasks, "_persist_generated_speaker_name_suggestions", None)
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _persist_generated_speaker_name_suggestions.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(tasks, "_persist_generated_speaker_name_suggestions_impl", None)
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError(
        "_persist_generated_speaker_name_suggestions_impl not found in tasks module"
    )


def _supersede_pending_speaker_name_suggestions_for_labels(*args, **kwargs):
    import sys

    tasks = sys.modules.get("backend.worker.tasks")
    if tasks:
        actual = getattr(
            tasks, "_supersede_pending_speaker_name_suggestions_for_labels", None
        )
        if (
            actual
            and getattr(actual, "__code__", None)
            is not _supersede_pending_speaker_name_suggestions_for_labels.__code__
        ):
            return actual(*args, **kwargs)
        impl = getattr(
            tasks, "_supersede_pending_speaker_name_suggestions_for_labels_impl", None
        )
        if impl:
            return impl(*args, **kwargs)
    raise RuntimeError(
        "_supersede_pending_speaker_name_suggestions_for_labels_impl not found in tasks module"
    )


__all__ = [name for name in globals() if not name.startswith("__")]
