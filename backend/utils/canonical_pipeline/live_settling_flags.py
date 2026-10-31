"""Each live line's settling flags, read once per row version (#776).

A line's settling flags sit inside its confidence payload, which PostgreSQL
compresses and stores out of line once the line holds a few words. Testing the
flags in SQL decompressed every live line of the capture, several times per
query, on every 2 s step, so a step cost more the longer the meeting ran.

Here a step first reads each live line's id, end and row version (``xmin`` and
``ctid``), which never touches a payload. Flags are read only for lines that
are new or changed since this process last read them, and the selections the
SQL made are then made over those flags. A row version never changes once
written, so a flag read for it stays true for as long as the version is
current, whichever process wrote the row: the results are the queries' own.
The flags are read with the queries' own SQL expressions, so their values
convert as they did.

Under the Transcript fence, where publication runs, no other writer commits
between the two reads. Outside it, where the step's snapshot runs, a commit
can land between them as it could between that snapshot's separate queries;
publication revalidates under the fence either way.

Other databases (the SQLite test suite) keep the original queries.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, literal_column
from sqlalchemy.orm import Session
from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.db_batching import bind_batches

from .live_settling_projection import SETTLING_KEY

_PAYLOAD = col(TranscriptUtterance.confidence_payload)[SETTLING_KEY]
# The expressions the queries tested, in the order of ``LiveLine``'s flags.
_FLAG_COLUMNS = (
    _PAYLOAD["version"].as_integer(),
    _PAYLOAD["text_locked"].as_boolean(),
    _PAYLOAD["line_locked"].as_boolean(),
    _PAYLOAD["line_closed"].as_boolean(),
)
_ROW_VERSION = (
    literal_column("transcript_utterances.xmin::text"),
    literal_column("transcript_utterances.ctid::text"),
)
# Captures whose flags this process keeps; a worker serves one at a time.
_RECORDINGS_KEPT = 8

_Flags = tuple[int | None, bool | None, bool | None, bool | None]
_Version = tuple[str, str]
_flags: OrderedDict[tuple[str, int], dict[int, tuple[_Version, _Flags]]] = OrderedDict()
_flags_lock = threading.Lock()


@dataclass(frozen=True)
class LiveLine:
    """One provisional live line's end and settling flags, as SQL reads them."""

    id: int
    end_ms: int
    version: int | None
    text_locked: bool | None
    line_locked: bool | None
    line_closed: bool | None


def flags_readable(session: Session) -> bool:
    """Whether row versions can be read, so the flags can be kept."""
    return session.get_bind().dialect.name == "postgresql"


def clear_flags() -> None:
    """Forget every kept flag (tests)."""
    with _flags_lock:
        _flags.clear()


def _live(recording_id: int) -> tuple[ColumnElement[bool], ...]:
    return (
        col(TranscriptUtterance.recording_id) == recording_id,
        col(TranscriptUtterance.state) == TranscriptUtteranceState.PROVISIONAL,
        col(TranscriptUtterance.source_kind) == "live",
    )


def live_lines(session: Session, recording_id: int) -> list[LiveLine]:
    """Every provisional live line of the recording, flags read at most once."""
    # A session may be bound to a connection; its engine names the database.
    key = (str(session.get_bind().engine.url), recording_id)
    scan = session.execute(
        select(TranscriptUtterance.id, TranscriptUtterance.end_ms, *_ROW_VERSION).where(
            *_live(recording_id)
        )
    ).all()
    with _flags_lock:
        known = dict(_flags.get(key, {}))
    current: dict[int, tuple[_Version, _Flags]] = {}
    ends: dict[int, int] = {}
    stale: list[int] = []
    for row_id, end_ms, xmin, ctid in scan:
        entry = known.get(row_id)
        if entry is not None and entry[0] == (xmin, ctid):
            current[row_id] = entry
            ends[row_id] = end_ms
        else:
            stale.append(row_id)
    for batch in bind_batches(stale):
        fresh = session.execute(
            select(
                TranscriptUtterance.id,
                TranscriptUtterance.end_ms,
                *_ROW_VERSION,
                *_FLAG_COLUMNS,
            )
            .where(*_live(recording_id))
            .where(col(TranscriptUtterance.id).in_(batch))
        ).all()
        for row_id, end_ms, xmin, ctid, *flags in fresh:
            current[row_id] = ((xmin, ctid), (flags[0], flags[1], flags[2], flags[3]))
            ends[row_id] = end_ms
    with _flags_lock:
        _flags[key] = current
        _flags.move_to_end(key)
        while len(_flags) > _RECORDINGS_KEPT:
            _flags.popitem(last=False)
    return [
        LiveLine(row_id, ends[row_id], *flags)
        for row_id, (_version, flags) in sorted(current.items())
    ]


def windowed_line_ids(lines: Sequence[LiveLine], window_start_ms: int) -> list[int]:
    """The ids ``live_rows`` selects for a window, in id order.

    Marked lines whose text or line is still open, lines ending inside the
    window, and the newest text-locked line as an anchor.
    """
    anchor = max(
        ((line.end_ms, line.id) for line in lines if line.text_locked is True),
        default=None,
    )
    return [
        line.id
        for line in lines
        if line.version == 1
        and (
            line.text_locked is not True
            or (line.line_locked is not True and line.line_closed is not True)
            or line.end_ms >= window_start_ms
            or (anchor is not None and line.id == anchor[1])
        )
    ]


def settled_frontier(lines: Sequence[LiveLine]) -> int:
    """The end of the newest unmarked or text-locked line, else 0."""
    return max(
        (
            line.end_ms
            for line in lines
            if line.version is None or line.text_locked is True
        ),
        default=0,
    )


def unmarked_frontier(lines: Sequence[LiveLine]) -> int:
    """The end of the newest unmarked (whole-line lane) line, else 0."""
    return max((line.end_ms for line in lines if line.version is None), default=0)
