"""Where on the audio timeline a capture is now, as the server can tell.

The audio timeline is the recording's audio with paused time left out: the
segments in sequence order, end to end. Live utterances and timeline notes are
anchored on it. During capture the server knows it only up to the end of the
last segment it has transcoded; while recording, the time since that segment
arrived is added, capped at about one segment, because a segment holds the
audio of the moments just before it was sent.

So the estimate trails the truth by the upload and transcode delay (typically
well under a second) and is exact while paused. After a resume it runs on from
the resume, not from the last segment, which arrived before the pause: run on
from that, it would sit at the cap ahead of the truth until the first new
segment lands. Every device viewing the recording reads the same figure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.models.recording import ClientStatus, RecordingStatus

# A segment is about 2 s of audio (frontend/src/lib/capture/controller.ts), so
# a gap longer than this since the last one means the next is late, not that
# more audio exists.
LIVE_AUDIO_EXTRAPOLATION_MAX_MS = 2500


def capture_is_advancing(
    status: RecordingStatus, client_status: Optional[ClientStatus]
) -> bool:
    """Whether the capturing browser is recording right now, not paused or stopping."""
    return status == RecordingStatus.UPLOADING and client_status in (
        None,
        ClientStatus.RECORDING,
    )


def estimate_live_audio_ms(
    *,
    last_chunk_end_ms: Optional[int],
    last_chunk_received_at: Optional[datetime],
    advancing: bool,
    now: datetime,
    resumed_at: Optional[datetime] = None,
) -> int:
    """The current position on the audio timeline, in ms. 0 before any segment.

    ``advancing`` is ``capture_is_advancing`` for the recording: only then does
    the figure run on past the last segment.

    ``resumed_at`` is when capture last resumed, if after the last segment
    arrived: the recording's ``last_activity_at``, which every resume sets.
    Uploads set it too, at most every 15 s and at about their own arrival, so
    when no resume followed the last segment it changes the figure by moments.
    """
    if last_chunk_end_ms is None:
        return 0
    if last_chunk_received_at is None or not advancing:
        return int(last_chunk_end_ms)
    since = last_chunk_received_at
    if resumed_at is not None and resumed_at > since:
        since = resumed_at
    elapsed_ms = int((now - since).total_seconds() * 1000)
    return int(last_chunk_end_ms) + min(
        max(elapsed_ms, 0), LIVE_AUDIO_EXTRAPOLATION_MAX_MS
    )
