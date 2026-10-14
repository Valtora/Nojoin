import logging

logger = logging.getLogger(__name__)

UPLOAD_CLOSED_DETAIL = "Recording is no longer accepting capture uploads"
STATUS_UPDATES_CLOSED_DETAIL = "Recording is no longer accepting capture status updates"
UNSUPPORTED_SEGMENT_MEDIA_DETAIL = "Unsupported audio segment format. Use audio/flac, audio/wav, audio/webm, audio/ogg, or audio/mp4 with a matching filename suffix."
SEGMENT_CONTENT_TYPE_SUFFIXES = {
    # Current browser capture sends FLAC. The other types are kept for a tab
    # still running a build from before the FLAC capture path.
    "audio/flac": ".flac",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
}
LOSSY_AUDIO_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".aac", ".webm", ".ogg", ".mp4", ".wma", ".opus"}
)
