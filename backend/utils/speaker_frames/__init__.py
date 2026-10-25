"""Opt-in frames of the shared screen, for the speaker study (#653).

When ``speaker_frame_sampling`` is ``owner``, the primary Owner can tick
**Screen study** at Start Meeting. The browser then uploads a small JPEG of the
shared screen about once a second while it changes, timed against the audio, so
an offline analysis can measure whether the meeting app's active-speaker
highlight would help speaker labels. Nothing in Nojoin reads the frames: no
model, no export, no backup, no MCP tool and no search index. They are deleted
14 days after the recording stops, at once when it is discarded or deleted, and
by ``python -m backend.utils.speaker_frames purge``.

Kept free of FastAPI so a worker can import it (the retention sweep).
"""

from .jpeg import InvalidFrame, jpeg_dimensions
from .settings import (
    ALLOWED_FRAME_RATES_HZ,
    DEFAULT_FRAME_RATE_HZ,
    SPEAKER_FRAME_RATE_KEY,
    SPEAKER_FRAME_SAMPLING_KEY,
    SPEAKER_FRAME_SAMPLING_OFF,
    SPEAKER_FRAME_SAMPLING_OWNER,
    frame_rate_hz,
    sampling_enabled,
    sampling_mode,
)
from .storage import (
    FRAMES_DIRNAME,
    HASHES_DIRNAME,
    INDEX_FILENAME,
    MAX_FRAME_BYTES,
    RETENTION,
    STATE_DIRNAME,
    TOTAL_CAP_BYTES,
    UPLOAD_WINDOW_AFTER_STOP,
    FrameSummary,
    InvalidRecordingId,
    StorageCapReached,
    StoredFrame,
    accepts_frames_after_stop,
    delete_after,
    delete_frames_for_recordings,
    delete_frames_off_loop,
    delete_recording_frames,
    frames_root,
    frames_summary,
    hashes_root,
    is_public_id,
    mark_recording_stopped,
    recording_frames_dir,
    recording_stopped_at,
    state_root,
    store_frame,
    total_bytes,
)

__all__ = [
    "ALLOWED_FRAME_RATES_HZ",
    "DEFAULT_FRAME_RATE_HZ",
    "FRAMES_DIRNAME",
    "HASHES_DIRNAME",
    "INDEX_FILENAME",
    "MAX_FRAME_BYTES",
    "RETENTION",
    "SPEAKER_FRAME_RATE_KEY",
    "SPEAKER_FRAME_SAMPLING_KEY",
    "SPEAKER_FRAME_SAMPLING_OFF",
    "SPEAKER_FRAME_SAMPLING_OWNER",
    "STATE_DIRNAME",
    "TOTAL_CAP_BYTES",
    "UPLOAD_WINDOW_AFTER_STOP",
    "FrameSummary",
    "InvalidFrame",
    "InvalidRecordingId",
    "StorageCapReached",
    "StoredFrame",
    "accepts_frames_after_stop",
    "delete_after",
    "delete_frames_for_recordings",
    "delete_frames_off_loop",
    "delete_recording_frames",
    "frame_rate_hz",
    "frames_root",
    "frames_summary",
    "hashes_root",
    "is_public_id",
    "jpeg_dimensions",
    "mark_recording_stopped",
    "recording_frames_dir",
    "recording_stopped_at",
    "sampling_enabled",
    "sampling_mode",
    "state_root",
    "store_frame",
    "total_bytes",
]
