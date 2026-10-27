"""An open line keeps identity on growth; real repartitioning replaces it."""

import copy
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.canonical_pipeline import live_settling_projection as projection
from backend.utils.canonical_pipeline.live_settling_lines import Partition


def _words(*identities: str, locked: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "id": identity,
            "order": index,
            "word": identity,
            "start_ms": index * 500,
            "end_ms": (index + 1) * 500,
            "locked": locked,
        }
        for index, identity in enumerate(identities)
    ]


def _row(public_id: str, *identities: str) -> TranscriptUtterance:
    return TranscriptUtterance(
        id=len(public_id),
        public_id=public_id,
        recording_id=1,
        sort_key=public_id,
        start_ms=0,
        end_ms=len(identities) * 500,
        text=" ".join(identities),
        speaker_label="UNKNOWN",
        source_kind="live",
        state=TranscriptUtteranceState.PROVISIONAL,
        revision=7,
        confidence_payload={
            "asr_segments": [{"words": _words(*identities)}],
            projection.SETTLING_KEY: {"version": 1, "line_closed": False},
        },
    )


def _part(*identities: str, locked: bool = False) -> Partition:
    return Partition(_words(*identities, locked=locked), "UNKNOWN", True)


@pytest.mark.parametrize("identities", [("a", "b", "c"), ("a", "corrected", "c")])
def test_open_growth_and_unlocked_tail_correction_keep_identity(
    identities: tuple[str, ...],
) -> None:
    row = _row("original", "a", "b")
    assert projection._partition_rows([row], [_part(*identities)]) == [row]


@pytest.mark.parametrize(
    "protection", ["closed", "locked", "manual_text", "manual_speaker"]
)
def test_growth_never_reuses_a_closed_or_protected_identity(protection: str) -> None:
    row = _row("protected", "a", "b")
    metadata = row.confidence_payload[projection.SETTLING_KEY]
    if protection == "closed":
        metadata["line_closed"] = True
    elif protection == "locked":
        metadata["line_locked"] = True
    elif protection == "manual_text":
        row.manual_text_locked = True
    else:
        row.manual_speaker_locked = True
    snapshot = copy.deepcopy(row.model_dump())
    assert projection._partition_rows([row], [_part("a", "b", "c")]) == [None]
    assert row.model_dump() == snapshot


def test_word_locks_alone_do_not_prevent_growing_an_open_line() -> None:
    row = _row("open", "a", "b")
    row.confidence_payload["asr_segments"][0]["words"] = _words("a", "b", locked=True)
    row.confidence_payload[projection.SETTLING_KEY]["text_locked"] = True
    assert projection._partition_rows([row], [_part("a", "b", "c")]) == [row]


@pytest.mark.parametrize(
    "old,new",
    [
        ([("a", "b")], [("a",), ("b", "c")]),
        ([("a",), ("b",)], [("a", "b", "c")]),
        ([("a", "b"), ("c", "d")], [("a",), ("b", "c", "d")]),
        ([("a", "b")], [("b", "c")]),
        ([("a", "b")], [("x", "y")]),
    ],
)
def test_split_merge_boundary_drift_and_new_first_word_replace_identity(
    old: list[tuple[str, ...]], new: list[tuple[str, ...]]
) -> None:
    rows = [_row(f"line-{index}", *words) for index, words in enumerate(old)]
    assert projection._partition_rows(rows, [_part(*words) for words in new]) == [
        None
    ] * len(new)


def test_exact_neighbour_is_reserved_before_reusing_the_growing_tail() -> None:
    first, tail = _row("first", "a", "b"), _row("tail", "c")
    assert projection._partition_rows(
        [first, tail], [_part("a", "b"), _part("c", "d")]
    ) == [first, tail]


def test_growth_emits_one_revision_event_then_lock_only_and_noop_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    events: list[tuple[str, str, int]] = []

    def event(_session: Session, row: TranscriptUtterance, kind: str) -> None:
        events.append((row.public_id, kind, row.revision))

    monkeypatch.setattr(projection, "_event", event)
    row = _row("original", "a", "b")
    original_sort_key = row.sort_key
    context = projection.PartitionWrite(1, [row], "generation", {})
    assert projection.write_partitions(session, context, [_part("a", "b", "c")]) == (
        [],
        ["original"],
        1,
    )
    assert row.revision == 8
    assert row.sort_key == original_sort_key
    assert row.state == TranscriptUtteranceState.PROVISIONAL
    assert row.text == "a b c"
    assert [word["id"] for word in projection.row_words(row)] == ["a", "b", "c"]
    assert events == [("original", "live_settling", 8)]
    assert projection.write_partitions(
        session, context, [_part("a", "b", "c", locked=True)]
    ) == ([], ["original"], 1)
    assert events[-1] == ("original", "live_settling", 9)
    assert all(word["locked"] for word in projection.row_words(row))
    snapshot = copy.deepcopy(row.model_dump())
    assert projection.write_partitions(
        session, context, [_part("a", "b", "c", locked=True)]
    ) == ([], ["original"], 0)
    assert row.model_dump() == snapshot
    assert len(events) == 2


@pytest.mark.parametrize("split", [True, False])
def test_real_split_or_merge_emits_tombstones_and_new_identities(
    monkeypatch: pytest.MonkeyPatch, split: bool
) -> None:
    session = MagicMock(spec=Session)
    events: list[tuple[str, str, int]] = []

    def event(_session: Session, row: TranscriptUtterance, kind: str) -> None:
        events.append((row.public_id, kind, row.revision))

    monkeypatch.setattr(projection, "_event", event)
    rows = (
        [_row("original", "a", "b")]
        if split
        else [_row("first", "a"), _row("second", "b")]
    )
    parts = [_part("a"), _part("b", "c")] if split else [_part("a", "b", "c")]
    old_ids = [row.public_id for row in rows]
    tombstones, active, changed = projection.write_partitions(
        session, projection.PartitionWrite(1, rows, "generation", {}), parts
    )
    assert tombstones == old_ids
    assert len(active) == len(parts)
    assert set(active).isdisjoint(old_ids)
    assert changed == len(old_ids) + len(parts)
    assert events == [
        *((identity, "supersede", 7) for identity in old_ids),
        *((identity, "created", 1) for identity in active),
    ]
    assert all(row.state == TranscriptUtteranceState.SUPERSEDED for row in rows)
