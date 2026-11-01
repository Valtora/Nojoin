"""Where the live lane carries on when the chunk it needs next is not on disk.

The lane drains chunks in sequence order from ``next_expected``. A chunk that is
quarantined (``.corrupt``) or failed to transcode (``.transcode_failed``) never
arrives, so the lane skips it at once, as the chunk cursor does. A chunk that is
simply absent may still be uploading or retrying, so the lane skips it only once
five later chunks (about 10 s) are on disk. The final pass includes a skipped
chunk that arrives late.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.processing.live_state import (
    _MAX_LIVE_SEQUENCE_OUTCOMES,
    _record_live_sequence_outcome,
)
from backend.utils.recording_audio_registration import sequence_failed

# Later chunks (about 10 s of 2 s chunks) that let the lane skip a missing one.
LIVE_GAP_SKIP_CHUNKS = 5


@dataclass(frozen=True)
class GapSkip:
    """The sequences ``first`` up to ``resume_at`` (exclusive), skipped by one run."""

    first: int
    resume_at: int

    def record(self, state: dict) -> None:
        """Mark the skipped sequences in ``state``, which keeps only the latest."""
        start = max(self.first, self.resume_at - _MAX_LIVE_SEQUENCE_OUTCOMES)
        for sequence in range(start, self.resume_at):
            _record_live_sequence_outcome(
                state, sequence=sequence, outcome="skipped", reason="gap_skipped"
            )

    def metric_payload(self) -> dict[str, Any]:
        # The range, not each sequence: a far-behind checkpoint skips thousands.
        return {
            "skipped_first": self.first,
            "skipped_last": self.resume_at - 1,
            "skipped_count": self.resume_at - self.first,
            "next_expected": self.resume_at,
        }


def _chunk_on_disk(temp_dir: Path, sequence: int) -> bool:
    return (temp_dir / f"{sequence}.wav").exists()


def find_gap_skip(temp_dir: Path, next_expected: int) -> GapSkip | None:
    """The skip past a missing head chunk, or None to wait for it."""
    head = next_expected
    while not _chunk_on_disk(temp_dir, head) and sequence_failed(temp_dir, head):
        head += 1
    if head > next_expected and _chunk_on_disk(temp_dir, head):
        return GapSkip(next_expected, head)
    later = sorted(
        int(path.stem)
        for path in temp_dir.glob("*.wav")
        if path.stem.isdigit() and int(path.stem) > next_expected
    )
    if len(later) < LIVE_GAP_SKIP_CHUNKS:
        return None
    return GapSkip(next_expected, later[0])
