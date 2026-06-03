# Package speakers endpoints init
from .router import router

# Import submodules to ensure routes are registered on the router
from . import routes_global
from . import routes_recording
from . import routes_voiceprint

# Re-export helper functions and classes for backwards compatibility
from .helpers import (
    _get_owned_recording,
    _canonical_transcript_writes_enabled,
    _require_recording_speaker_mutations_supported,
    _require_recordings_support_speaker_mutations,
    _copy_transcript_segments,
    _load_segments_for_speaker_work,
    _persist_segments_for_speaker_work,
    _serialize_recording_speakers,
    _mark_pending_speaker_suggestions_superseded,
    _merge_local_speakers,
    SpeakerUpdate,
    MergeRequest,
    MergeRequestLabels,
    VoiceprintAction,
    SpeakerSegment,
    SegmentSelection,
    VoiceprintResult,
    SpeakerSplitRequest,
    SpeakerColorUpdate,
)

# Re-export top-level imports that were present in the original speakers.py
from backend.celery_app import celery_app
from backend.utils.config_manager import config_manager
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.canonical_pipeline import recording_ready_for_canonical_backfill

# Dynamically construct __all__ to include all names from this package's namespace
__all__ = [name for name in globals() if not name.startswith('__')]
