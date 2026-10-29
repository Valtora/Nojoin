"""Materialise deferred live snapshots for explicit compatibility mutations."""

from uuid import uuid4

from sqlalchemy.orm import Session

from backend.models.transcript import Transcript

from .constants import _load_transcript, _sort_key_for_index


def has_deferred_live_projection(transcript: Transcript) -> bool:
    """Marked capture episodes keep their compatibility snapshot deferred."""
    state = transcript.settling_state or {}
    return bool(state.get("eligible") and state.get("phase") in {"live", "paused"})


def capture_segment_sort_key(transcript: Transcript, index: int, start_ms: int) -> str:
    """Keep marked CPU and rolling lines on the same capture-clock ordering."""
    if has_deferred_live_projection(transcript):
        return f"{start_ms:020d}:{uuid4().hex[:8]}"
    return _sort_key_for_index(index)


def prepare_live_compatibility_mutation(session: Session, recording_id: int) -> bool:
    """Refresh only on a requested edit, under the transcript publication fence."""
    from .core import refresh_transcript_projection_from_canonical

    transcript = _load_transcript(session, recording_id, lock=True)
    if transcript is None or not has_deferred_live_projection(transcript):
        return False
    refresh_transcript_projection_from_canonical(session, recording_id)
    return True
