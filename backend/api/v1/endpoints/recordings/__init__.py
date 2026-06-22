# Package recordings endpoints init
from backend.celery_app import celery_app
from backend.models.recording import RecordingStatus
from backend.processing.llm_services import (
    get_llm_backend,
    get_llm_backend_with_secondary,
)
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.services.recording_identity_service import (
    get_recording_by_public_id,
    get_recordings_by_public_ids,
)
from backend.utils.audio import (
    LOSSY_AUDIO_BITRATE_FLOOR_BITS_PER_SECOND,
    concatenate_binary_files,
    concatenate_media_files,
    concatenate_wavs,
    get_audio_duration,
)
from backend.utils.canonical_pipeline import (
    build_transcript_segments_for_read,
    build_transcript_text_for_read,
    filter_recording_speakers_for_public_read,
)
from backend.utils.config_manager import config_manager, is_llm_available
from backend.utils.processing_eta import estimate_processing_eta
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.recording_audio_sync import (
    BROWSER_AUDIO_SEGMENT_SUFFIXES,
    find_missing_chunk_sequences,
    find_pending_recording_upload_sequences,
    list_recording_audio_chunks,
    sync_recording_audio_chunks_from_directory,
    sync_recording_audio_chunks_from_entries,
    sync_recording_audio_window_manifests,
)

# Re-export top-level imports that were present in the original recordings.py
# to preserve backwards-compatibility for test monkeypatching
from backend.utils.recording_storage import (
    RECORDING_UPLOAD_RETENTION_HOURS,
    delete_recording_artifacts,
    move_recording_upload_to_failed,
    recording_upload_temp_dir,
    recordings_root_dir,
)
from backend.utils.speaker_label_manager import SpeakerLabelManager
from backend.utils.time import utc_now
from backend.utils.upload_limit import (
    UPLOAD_LIMIT_LEGACY_RECORDING,
    UPLOAD_LIMIT_SEGMENT,
    stream_and_validate_upload,
)

# Import submodules to ensure routes are registered on the router
from . import (
    routes_actions,
    routes_batch_init,
    routes_capture,
    routes_import_upload,
    routes_query,
)
from .constants import (
    LOSSY_AUDIO_SUFFIXES,
    SEGMENT_CONTENT_TYPE_SUFFIXES,
    STATUS_UPDATES_CLOSED_DETAIL,
    UNSUPPORTED_SEGMENT_MEDIA_DETAIL,
    UPLOAD_CLOSED_DETAIL,
)

# Re-export helper functions and classes for backwards compatibility (especially test cases)
from .helpers import (
    _assert_recording_owner,
    _bootstrap_import_audio_windows,
    _browser_master_output_path,
    _build_active_recording_conflict,
    _chunk_cleanup_deadline,
    _chunk_idempotency_key,
    _enforce_lossy_audio_bitrate_floor,
    _ensure_recording_accepts_status_updates,
    _ensure_recording_accepts_uploads,
    _ensure_recording_can_finalize_upload,
    _estimated_audio_bitrate_bits_per_second,
    _find_missing_chunk_sequences,
    _find_pending_transcode_sequences,
    _get_active_capture_recording_for_user,
    _get_last_uploaded_sequence,
    _get_owned_calendar_event,
    _get_owned_recording,
    _list_recording_audio_chunks,
    _list_staged_browser_master_segments,
    _mark_recording_audio_chunks_failed,
    _mark_recording_audio_chunks_ready_for_cleanup,
    _mark_recording_upload_error,
    _normalize_segment_content_type,
    _recording_has_proxy,
    _requeue_for_processing,
    _reset_generated_recording_state,
    _resolve_browser_master_suffix,
    _resolve_segment_upload_suffix,
    _should_hide_in_flight_transcript_content,
    _stage_import_audio_chunk,
    _sync_recording_audio_chunks_from_directory,
    _sync_recording_audio_chunks_from_entries,
    _sync_recording_audio_window_manifests,
    _transcode_pending_browser_segments_for_finalize,
    generate_default_meeting_name,
    get_initial_proxy_path,
    get_ordinal_suffix,
)
from .router import router
from .routes_batch_init import BatchRecordingIds

# Dynamically construct __all__ to include all names from this package's namespace
__all__ = [name for name in globals() if not name.startswith("__")]
