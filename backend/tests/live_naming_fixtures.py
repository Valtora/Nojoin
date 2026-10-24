"""A live capture's lines, labelled by the live label step, for the naming tests.

Times are seconds. ``LIVE_PROVISIONAL_1`` has a locked turn at 0-6 s and a
longer unlocked one at 9.5-20 s; ``LIVE_PROVISIONAL_2`` has nothing locked, a
3 s turn at 6-9 s and a 1 s one at 21-22 s; ``LIVE_PROVISIONAL_3`` speaks at
30-33 s and 34-36 s with an unlabelled line between them.
"""

from __future__ import annotations

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.utils.canonical_pipeline import append_utterances_from_segments
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)

ONE = "LIVE_PROVISIONAL_1"
TWO = "LIVE_PROVISIONAL_2"
THREE = "LIVE_PROVISIONAL_3"

# (start, end, label, locked)
LIVE_LINES = [
    (0.0, 4.0, ONE, True),
    (4.0, 6.0, ONE, True),
    (6.0, 9.0, TWO, False),
    (9.5, 20.0, ONE, False),
    (20.0, 21.0, "UNKNOWN", False),
    (21.0, 22.0, TWO, False),
    (30.0, 33.0, THREE, False),
    (33.0, 34.0, "UNKNOWN", False),
    (34.0, 36.0, THREE, False),
]


def seed_live_lines(
    session,
    recording_id: int = 1,
    lines: list[tuple[float, float, str, bool]] = LIVE_LINES,
) -> None:
    """Append the lines as the live lane does, then label them as its step does."""
    append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[
            {
                "id": f"live-{recording_id}-{index}",
                "start": start,
                "end": end,
                # Enough words between them for Meeting Edge to run on.
                "text": f"live line {index} with a few more words to say",
                "speaker": "UNKNOWN",
                "provisional": True,
                "segment_source": "live",
                "speaker_state": "provisional",
            }
            for index, (start, end, _, _) in enumerate(lines)
        ],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        state_override=TranscriptUtteranceState.PROVISIONAL,
        trigger_source="worker",
    )
    decisions = {
        int(start * 1000): LiveLabelDecision(label, locked=locked, share=0.9)
        for start, _, label, locked in lines
    }
    apply_live_speaker_labels(
        session,
        recording_id=recording_id,
        settled_ms=0,
        decide=lambda start_ms, end_ms: decisions[start_ms],
    )
    session.commit()
