# Package recordings endpoints init
from .router import router

# Import submodules to ensure routes are registered on the router
from . import routes_batch_init
from . import routes_capture
from . import routes_import_upload
from . import routes_query
from . import routes_actions

# Re-export helper functions and classes for backwards compatibility (especially test cases)
from .helpers import (
    _recording_has_proxy,
    _estimated_audio_bitrate_bits_per_second,
    _enforce_lossy_audio_bitrate_floor,
    _list_staged_browser_master_segments,
    _resolve_browser_master_suffix,
    _browser_master_output_path,
    _mark_recording_upload_error,
    _should_hide_in_flight_transcript_content,
    _get_owned_recording,
    _assert_recording_owner,
    _get_active_capture_recording_for_user,
    _get_last_uploaded_sequence,
    _build_active_recording_conflict,
    get_initial_proxy_path,
    _chunk_cleanup_deadline,
    _chunk_idempotency_key,
    _normalize_segment_content_type,
    _resolve_segment_upload_suffix,
    _sync_recording_audio_chunks_from_entries,
    _sync_recording_audio_chunks_from_directory,
    _stage_import_audio_chunk,
    _bootstrap_import_audio_windows,
    _list_recording_audio_chunks,
    _sync_recording_audio_window_manifests,
    _find_missing_chunk_sequences,
    _find_pending_transcode_sequences,
    _transcode_pending_browser_segments_for_finalize,
    _mark_recording_audio_chunks_ready_for_cleanup,
    _mark_recording_audio_chunks_failed,
    _reset_generated_recording_state,
    _requeue_for_processing,
    get_ordinal_suffix,
    _ensure_recording_accepts_uploads,
    _ensure_recording_accepts_status_updates,
    _ensure_recording_can_finalize_upload,
    generate_default_meeting_name,
    _get_owned_calendar_event,
)

from .constants import (
    UPLOAD_CLOSED_DETAIL,
    STATUS_UPDATES_CLOSED_DETAIL,
    UNSUPPORTED_SEGMENT_MEDIA_DETAIL,
    SEGMENT_CONTENT_TYPE_SUFFIXES,
    LOSSY_AUDIO_SUFFIXES,
)

from .routes_batch_init import BatchRecordingIds

# Re-export top-level imports that were present in the original recordings.py
# to preserve backwards-compatibility for test monkeypatching
from backend.utils.recording_storage import (
    RECORDING_UPLOAD_RETENTION_HOURS,
    delete_recording_artifacts,
    move_recording_upload_to_failed,
    recording_upload_temp_dir,
    recordings_root_dir,
)
from backend.utils.audio import (
    LOSSY_AUDIO_BITRATE_FLOOR_BITS_PER_SECOND,
    concatenate_binary_files,
    concatenate_media_files,
    concatenate_wavs,
    get_audio_duration,
)
from backend.processing.llm_services import get_llm_backend, get_llm_backend_with_secondary
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.speaker_label_manager import SpeakerLabelManager
from backend.utils.time import utc_now
from backend.utils.config_manager import config_manager, is_llm_available
from backend.utils.upload_limit import (
    stream_and_validate_upload,
    UPLOAD_LIMIT_SEGMENT,
    UPLOAD_LIMIT_LEGACY_RECORDING,
)
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.processing_eta import estimate_processing_eta
from backend.services.recording_identity_service import get_recording_by_public_id, get_recordings_by_public_ids
from backend.utils.canonical_pipeline import (
    build_transcript_segments_for_read,
    build_transcript_text_for_read,
    filter_recording_speakers_for_public_read,
)
from backend.utils.recording_audio_sync import (
    BROWSER_AUDIO_SEGMENT_SUFFIXES,
    find_missing_chunk_sequences,
    find_pending_recording_upload_sequences,
    list_recording_audio_chunks,
    sync_recording_audio_chunks_from_directory,
    sync_recording_audio_chunks_from_entries,
    sync_recording_audio_window_manifests,
)
from backend.models.recording import RecordingStatus
from backend.celery_app import celery_app

# Dynamically construct __all__ to include all names from this package's namespace
__all__ = [name for name in globals() if not name.startswith('__')]
