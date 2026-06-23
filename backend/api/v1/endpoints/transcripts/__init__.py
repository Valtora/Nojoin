# Package transcripts endpoints init
# Re-export top-level imports that were present in the original transcripts.py
# to preserve backwards-compatibility for test monkeypatching/patching.
from backend.celery_app import celery_app
from backend.processing.llm_services import get_llm_backend_with_secondary
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.config_manager import config_manager, is_meeting_edge_enabled

# Import submodules to ensure routes are registered on the router
from . import (
    routes_chat,
    routes_export,
    routes_notes,
    routes_segments,
    routes_utterances,
)

# Re-export Pydantic models and helper functions for backwards compatibility.
from .helpers import (
    ChatRequest,
    FindReplaceRequest,
    MeetingEdgeFocusUpdate,
    NotesUpdate,
    TranscriptSegmentSpeakerUpdate,
    TranscriptSegmentsUpdate,
    TranscriptSegmentTextUpdate,
    TranscriptUtteranceListRead,
    TranscriptUtteranceRead,
    TranscriptUtteranceSpeakerPatch,
    TranscriptUtteranceTextPatch,
    UserNotesUpdate,
    _apply_find_replace,
    _build_speaker_map,
    _canonical_transcript_writes_enabled,
    _dispatch_meeting_edge_refresh,
    _find_segment_index_by_public_id,
    _format_transcript_text,
    _generate_docx_export,
    _generate_full_markdown,
    _generate_pdf_export,
    _get_owned_recording,
    _get_recording_speaker_display_name,
    _get_recording_transcript,
    _get_segment_revision,
    _parse_markdown_line,
    _require_recording_transcript_mutations_supported,
    _sanitize_filename,
)
from .router import router

# Dynamically construct __all__ to include all names from this package's namespace
__all__ = [name for name in globals() if not name.startswith("__")]
