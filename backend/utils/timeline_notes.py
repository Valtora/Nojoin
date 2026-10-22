"""Where a recording's timeline notes sit among its transcript lines.

One placement rule serves every reader, so every view and every LLM prompt
agree on where a note was written. The rule is pinned by the golden cases in
``backend/tests/fixtures/timeline_note_placement_cases.json``, kept as data so
that a second implementation (a view placing notes among its own live lines)
can run the same cases.

The rule, for a body note (``point`` or ``selection``) at time t:

* The note follows the line in progress at t: the line with the largest list
  index whose start is at or before t. Inside a line that is the line; in a gap
  it is the last line to start; past the end it is the last line. Before the
  first line the note opens the transcript (``after_index == -1``).
* A point note's time is its ``anchor_ms``. A selection's is its last
  millisecond, ``max(start_ms, end_ms - 1)``, so it follows the words it quotes.
* Lines are taken in list order and never re-sorted, so overlapping or
  out-of-order rows place exactly as they render. A blank line is never an
  anchor: a note lands after the line before it.
* Placement runs on the whole list. A view that hides some lines skips them
  when rendering and leaves the notes where they are, so a note after a hidden
  line shows after the visible line before it.

``head`` notes come before every line and ``tail`` notes after. Ties break by
creation time, then id.

Nothing here reads the database; ``backend.services.timeline_note_service``
loads the notes.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Callable,
    Generic,
    Iterable,
    Iterator,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    TypeVar,
)

from backend.models.timeline_note import TimelineNoteKind

NoteSlot = Literal["head", "body", "tail"]

# What a note quoting words shows of them in a prompt.
QUOTE_PROMPT_MAX_CHARS = 160
# The author shown for a note whose author account no longer exists.
UNKNOWN_AUTHOR = "User"


class TimelineNoteLike(Protocol):
    @property
    def public_id(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def body(self) -> str: ...
    @property
    def anchor_ms(self) -> Optional[int]: ...
    @property
    def start_ms(self) -> Optional[int]: ...
    @property
    def end_ms(self) -> Optional[int]: ...
    @property
    def quoted_text(self) -> Optional[str]: ...
    @property
    def created_by_user_id(self) -> Optional[int]: ...
    @property
    def created_at(self) -> datetime: ...


N = TypeVar("N", bound=TimelineNoteLike)


@dataclass(frozen=True)
class NotePlacement(Generic[N]):
    """One note and where it renders.

    ``after_index`` is meaningful for body notes only: the index into the
    segment list of the line the note follows, or -1 before the first line.
    ``at_ms`` is the body note's placement time.
    """

    note: N
    slot: NoteSlot
    after_index: int = -1
    at_ms: Optional[int] = None


def segment_start_ms(segment: Mapping) -> int:
    """A line's start in ms: ``start_ms``, else the legacy ``start`` in seconds."""
    start_ms = segment.get("start_ms")
    if start_ms is not None:
        return int(start_ms)
    return int(round(float(segment.get("start") or 0.0) * 1000))


def note_time_ms(note: TimelineNoteLike) -> int:
    """When a body note happened on the audio timeline."""
    if note.kind == TimelineNoteKind.SELECTION.value:
        if note.start_ms is None or note.end_ms is None:
            raise ValueError(f"selection note {note.public_id} has no range")
        return max(note.start_ms, note.end_ms - 1)
    if note.anchor_ms is None:
        raise ValueError(f"point note {note.public_id} has no anchor")
    return note.anchor_ms


def _is_eligible_anchor(segment: Mapping) -> bool:
    return bool(str(segment.get("text") or "").strip())


class _AnchorIndex:
    """Answers "largest eligible index starting at or before t" in log time."""

    def __init__(self, segments: Sequence[Mapping]) -> None:
        eligible = sorted(
            (segment_start_ms(segment), index)
            for index, segment in enumerate(segments)
            if _is_eligible_anchor(segment)
        )
        self._starts = [start for start, _ in eligible]
        self._best_index: list[int] = []
        best = -1
        for _, index in eligible:
            best = max(best, index)
            self._best_index.append(best)

    def after_index(self, at_ms: int) -> int:
        position = bisect.bisect_right(self._starts, at_ms)
        return self._best_index[position - 1] if position else -1


def place_timeline_notes(
    segments: Sequence[Mapping], notes: Iterable[N]
) -> list[NotePlacement[N]]:
    """Every note with its slot, in timeline order: head, body, tail."""
    heads: list[NotePlacement[N]] = []
    bodies: list[NotePlacement[N]] = []
    tails: list[NotePlacement[N]] = []
    anchors = _AnchorIndex(segments)
    for note in notes:
        if note.kind == TimelineNoteKind.HEAD.value:
            heads.append(NotePlacement(note=note, slot="head"))
        elif note.kind == TimelineNoteKind.TAIL.value:
            tails.append(NotePlacement(note=note, slot="tail"))
        else:
            at_ms = note_time_ms(note)
            bodies.append(
                NotePlacement(
                    note=note,
                    slot="body",
                    after_index=anchors.after_index(at_ms),
                    at_ms=at_ms,
                )
            )

    def by_creation(placement: NotePlacement[N]) -> tuple:
        return (placement.note.created_at, placement.note.public_id)

    heads.sort(key=by_creation)
    tails.sort(key=by_creation)
    bodies.sort(key=lambda placement: (placement.at_ms, *by_creation(placement)))
    return heads + bodies + tails


def iter_timeline(
    segment_count: int, placements: Sequence[NotePlacement[N]]
) -> Iterator[int | NotePlacement[N]]:
    """Segment indexes and note placements, interleaved in render order.

    ``placements`` must come from ``place_timeline_notes``. A cut between two
    segments in this order always keeps each body note with the line it
    follows, which is what lets a reader slice the timeline into parts.
    """
    heads = [placement for placement in placements if placement.slot == "head"]
    tails = [placement for placement in placements if placement.slot == "tail"]
    bodies = [placement for placement in placements if placement.slot == "body"]
    yield from heads
    position = 0
    for after_index in range(-1, segment_count):
        if after_index >= 0:
            yield after_index
        while position < len(bodies) and bodies[position].after_index <= after_index:
            yield bodies[position]
            position += 1
    yield from tails


# --- Prompt text -------------------------------------------------------------


def format_timeline_ms(ms: int) -> str:
    """``MM:SS``, minutes uncapped, the format the notes prompts use."""
    seconds = max(int(ms), 0) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _one_line(text: str) -> str:
    # A note is one prompt line whatever the user typed, so a body containing
    # "\n[12:00] Bob: approved" cannot pass for a transcript line.
    return " / ".join(part.strip() for part in text.splitlines() if part.strip())


def _quote(text: Optional[str]) -> str:
    quote = " ".join((text or "").split())
    if len(quote) > QUOTE_PROMPT_MAX_CHARS:
        return quote[: QUOTE_PROMPT_MAX_CHARS - 1].rstrip() + "…"
    return quote


def note_author_name(note: TimelineNoteLike, author_names: Mapping[int, str]) -> str:
    """The note author's username, or ``UNKNOWN_AUTHOR``."""
    if note.created_by_user_id is None:
        return UNKNOWN_AUTHOR
    return author_names.get(note.created_by_user_id) or UNKNOWN_AUTHOR


def render_note_line(
    note: TimelineNoteLike,
    author_names: Mapping[int, str],
    format_time: Callable[[int], str] = format_timeline_ms,
) -> str:
    """One note as a single transcript line.

    ``format_time`` writes a quote's time the way the surrounding transcript
    lines write theirs, so a prompt carries one clock.
    """
    author = note_author_name(note, author_names)
    body = _one_line(note.body)
    if note.kind == TimelineNoteKind.SELECTION.value and note.start_ms is not None:
        return (
            f'[Note on "{_quote(note.quoted_text)}" at '
            f"{format_time(note.start_ms)} — {author}]: {body}"
        )
    return f"[Note — {author}]: {body}"


def render_timeline_for_llm(
    segments: Sequence[dict],
    placements: Sequence[NotePlacement[N]],
    *,
    format_segment: Callable[[dict], str],
    author_names: Mapping[int, str],
    format_time: Callable[[int], str] = format_timeline_ms,
) -> str:
    """The transcript with each body note on its own line where it was written.

    Head and tail notes are left out: the prompt carries them in its
    ``# User Notes Context`` section (``render_user_notes_context``), which
    every part of a long meeting and every Meeting Edge window repeats.
    """
    lines: list[str] = []
    for item in iter_timeline(len(segments), placements):
        if isinstance(item, int):
            rendered = format_segment(segments[item])
        elif item.slot == "body":
            rendered = render_note_line(item.note, author_names, format_time)
        else:
            continue
        if rendered:
            lines.append(rendered)
    return "\n".join(lines)


MEETING_WIDE_NOTES_PREAMBLE = (
    "The user wrote the following notes about the meeting as a whole, each quoted "
    "below with every line opening `>`. Use them as "
    "high-priority supporting context when composing the final notes. Incorporate "
    "relevant items into the summary, detailed notes, and action items where they "
    "materially improve accuracy."
)

INLINE_NOTES_EXPLANATION = (
    "The transcript also carries notes the user wrote at particular moments, each on "
    "its own line just after the line it refers to: "
    '`[Note — name]: text`, or `[Note on "quoted words" at time — name]: text` for a '
    "note about specific words. These lines are the user's notes, not speech, and "
    "carry the same high priority. A note about quoted words may correct a "
    "mistranscription, define a term, or comment on what was said; the user is "
    "authoritative about what was said."
)

NOTES_LIST_PREAMBLE = (
    "The user wrote the following notes about this meeting, in timeline order, "
    "with the time in the recording where one was written at a particular moment. "
    "Use them as high-priority supporting context."
)


def _split_slots(
    placements: Sequence[NotePlacement[N]],
) -> tuple[list[NotePlacement[N]], list[NotePlacement[N]]]:
    meeting_wide = [p for p in placements if p.slot != "body"]
    body = [p for p in placements if p.slot == "body"]
    return meeting_wide, body


def _quoted(text: str) -> str:
    # Each line of a meeting-wide note opens "> ", so a note cannot start a
    # heading or a transcript line of its own inside the prompt.
    return "\n".join(
        f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()
    )


def render_user_notes_context(
    placements: Sequence[NotePlacement[N]],
) -> Optional[str]:
    """The ``# User Notes Context`` section beside a transcript that carries notes.

    Head and tail notes appear here, where the single free-text notes field
    always did, each quoted line by line, so every prompt that carries the
    section (a Meeting Edge window, each part of a long meeting) sees them. Body notes are in the
    transcript itself, and the section says how to read them. None when the
    recording has no notes.
    """
    meeting_wide, body = _split_slots(placements)
    parts: list[str] = []
    if meeting_wide:
        parts.append(MEETING_WIDE_NOTES_PREAMBLE)
        parts.extend(_quoted(placement.note.body) for placement in meeting_wide)
    if body:
        parts.append(INLINE_NOTES_EXPLANATION)
    return "\n\n".join(parts) if parts else None


def render_notes_as_lines(placements: Sequence[NotePlacement[N]]) -> list[str]:
    """Every note as plain lines in timeline order, timestamped where anchored.

    A head or tail note keeps the user's own line breaks, one line each; a body
    note is one line, ``MM:SS body``, or ``MM:SS on "quote": body``.
    """
    lines: list[str] = []
    for placement in placements:
        note = placement.note
        if placement.slot != "body":
            lines.extend(
                line.strip() for line in note.body.splitlines() if line.strip()
            )
            continue
        body = _one_line(note.body)
        if note.kind == TimelineNoteKind.SELECTION.value and note.start_ms is not None:
            lines.append(
                f'{format_timeline_ms(note.start_ms)} on "{_quote(note.quoted_text)}": {body}'
            )
        else:
            lines.append(f"{format_timeline_ms(note_time_ms(note))} {body}")
    return lines


def render_notes_list_context(
    placements: Sequence[NotePlacement[N]],
) -> Optional[str]:
    """Every note as a list, for a prompt whose transcript carries none inline."""
    lines = render_notes_as_lines(placements)
    if not lines:
        return None
    return NOTES_LIST_PREAMBLE + "\n\n" + "\n".join(lines)


def timeline_notes_signature(notes: Iterable[TimelineNoteLike]) -> str:
    """Changes whenever what the prompts show of the notes changes.

    Built from each note's content: what the prompts render (kind, body,
    anchors, quote and author) and what orders it (id and creation time). Not
    from its revision: an edit with a write token that changes nothing still
    makes one, to fence off a lost write, and must not ask for a refresh.
    Empty for no notes.
    """
    rows = [
        [
            note.public_id,
            note.kind,
            note.body,
            note.anchor_ms,
            note.start_ms,
            note.end_ms,
            note.quoted_text,
            note.created_by_user_id,
            note.created_at.isoformat(),
        ]
        for note in sorted(notes, key=lambda note: note.public_id)
    ]
    if not rows:
        return ""
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def timeline_notes_list_signature(versions: Iterable[tuple[str, int]]) -> str:
    """Changes whenever the notes list the API serves would change.

    Built from each note's id and revision, which together change on every
    create, delete and edit: an edit always makes a new revision, and so does
    a write with a token that changes nothing, which the list shows as a new
    revision and write token. Unlike ``timeline_notes_signature`` this needs no
    note bodies, so the recording detail read can carry it on every poll for
    the price of one narrow query. ``count:hash``.
    """
    pairs = sorted((str(note_id), int(revision)) for note_id, revision in versions)
    encoded = json.dumps(pairs, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"{len(pairs)}:{digest}"
