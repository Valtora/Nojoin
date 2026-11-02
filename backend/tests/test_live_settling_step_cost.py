"""A settling step's line matching and closure checks stay linear in open lines.

Wall-clock bounds are flaky on shared CI runners, so this counts the Python loop
iterations (backward jumps, via ``sys.monitoring``) the two helpers execute. The
count is deterministic and does not depend on machine speed. At 3,000 closed lines
of 12 words, the linear helpers take about 100 iterations per line; a per-line
rescan of all words or all partitions takes thousands (9 million and more in total).
"""

import sys
from collections.abc import Callable
from typing import Any

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.canonical_pipeline.live_settling import _line_boundaries
from backend.utils.canonical_pipeline.live_settling_lines import Partition
from backend.utils.canonical_pipeline.live_settling_projection import (
    SETTLING_KEY,
    _partition_rows,
)

LINES = 3_000
WORDS_PER_LINE = 12
# About three times what the linear code needs, a tenth of what one rescan costs.
LOOP_BUDGET = 300 * LINES


def _meeting() -> tuple[
    list[TranscriptUtterance], list[Partition], list[dict[str, Any]]
]:
    rows, partitions, current = [], [], []
    for line in range(LINES):
        start = line * 3_600
        end = start + (WORDS_PER_LINE - 1) * 270 + 250
        words: list[dict[str, Any]] = [
            {
                "id": f"w{line}-{index}",
                "word": f" word{index}",
                "locked": True,
                "start_ms": start + index * 270,
                "end_ms": start + index * 270 + 250,
            }
            for index in range(WORDS_PER_LINE)
        ]
        speaker = f"LIVE_PROVISIONAL_{line % 80 + 1}"
        rows.append(
            TranscriptUtterance(
                id=line + 1,
                public_id=f"line-{line}",
                recording_id=1,
                sort_key=f"{line:06d}",
                start_ms=start,
                end_ms=end,
                text="",
                speaker_label=speaker,
                source_kind="live",
                state=TranscriptUtteranceState.PROVISIONAL,
                revision=1,
                confidence_payload={
                    "asr_segments": [{"words": words}],
                    SETTLING_KEY: {
                        "version": 1,
                        "boundary_locked": True,
                        "closure_reason": "pause",
                        "closed_at_ms": end,
                    },
                },
            )
        )
        partitions.append(Partition(words, speaker, False))
        current.extend(words)
    return rows, partitions, current


def _loop_iterations(run: Callable[[], object]) -> int:
    """Backward jumps executed by ``run``; counting stops once over budget."""
    monitoring = sys.monitoring
    tool = next((i for i in range(1, 6) if monitoring.get_tool(i) is None), None)
    assert tool is not None, "no free sys.monitoring tool id"
    count = 0

    def jumped(*_: object) -> None:
        nonlocal count
        count += 1
        if count > LOOP_BUDGET:
            monitoring.set_events(tool, 0)

    monitoring.use_tool_id(tool, "settling-step-cost")
    try:
        monitoring.register_callback(tool, monitoring.events.JUMP, jumped)
        monitoring.set_events(tool, monitoring.events.JUMP)
        run()
    finally:
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, monitoring.events.JUMP, None)
        monitoring.free_tool_id(tool)
    return count


def test_three_thousand_open_lines_match_and_close_in_linear_work() -> None:
    rows, partitions, current = _meeting()
    state = {"max_segment_ms": 30_000}
    results: dict[str, Any] = {}

    def step() -> None:
        results["matches"] = _partition_rows(rows, partitions)
        results["boundaries"] = _line_boundaries(rows, state, None, current)

    iterations = _loop_iterations(step)

    # Every unchanged line keeps its row, and every closed end is still recognised.
    assert all(a is b for a, b in zip(results["matches"], rows, strict=True))
    assert len(results["boundaries"].closed_ends) == LINES
    assert iterations <= LOOP_BUDGET, (
        f"{iterations} loop iterations for {LINES} lines (budget {LOOP_BUDGET})"
    )
