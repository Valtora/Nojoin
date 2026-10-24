"""Assemble an export document from a meeting's parts.

The order is fixed whatever the reader ticked: notes, then analytics, then the
transcript. Notes are what most readers came for, analytics qualify how the
meeting went, and the transcript is the evidence for both. The reader's own
timeline notes sit in the transcript: notes on the whole meeting first and
last, the rest after the line they fall on. Generated notes also end with them,
under ``## User Notes``. The reader's captures sit in the transcript too, each
placed as a note at its time would be. A file exported without the reader's
notes leaves out all three.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.models.timeline_note import TimelineNoteKind
from backend.utils.capture_images import RenderedImage
from backend.utils.live_speaker_labels import speaker_display_name
from backend.utils.meeting_notes import split_user_notes_section
from backend.utils.timeline_notes import (
    NotePlacement,
    TimelineNoteLike,
    iter_timeline,
    note_time_ms,
    place_timeline_notes,
)

from .analytics_blocks import analytics_blocks
from .formatting import (
    format_duration,
    format_meeting_date,
    format_meeting_datetime,
    format_transcript_time,
)
from .markdown_blocks import notes_to_blocks
from .model import (
    LINE_BREAK,
    Block,
    Callout,
    ExportDocument,
    ImageBlock,
    MetaItem,
    Paragraph,
    Section,
    Span,
    TranscriptLine,
    plain,
)

SECTION_ORDER = ("notes", "analytics", "transcript")
SECTION_TITLES = {
    "notes": "Meeting notes",
    "analytics": "Analytics",
    "transcript": "Transcript",
}


@dataclass(frozen=True)
class ExportCapture:
    """A capture as the transcript places it: a point note at its time.

    It has the shape ``place_timeline_notes`` reads, so captures and notes are
    placed by one rule and interleaved by time. ``image`` is the picture at
    print size, or None when its file is gone or cannot be read.
    """

    public_id: str
    anchor_ms: int
    created_at: datetime
    title: str
    image: RenderedImage | None
    kind: str = TimelineNoteKind.POINT.value
    body: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    quoted_text: str | None = None
    created_by_user_id: int | None = None


@dataclass(frozen=True)
class MeetingParts:
    """Everything an export can draw on, already loaded and resolved."""

    title: str
    created_at: datetime | None
    timezone_name: str
    duration_seconds: float | None
    speaker_names: tuple[str, ...]
    notes: str | None = None
    segments: Sequence[Mapping[str, Any]] = ()
    # Diarisation label -> display name, and -> hex colour.
    speaker_names_by_label: Mapping[str, str] = field(default_factory=dict)
    speaker_colors_by_label: Mapping[str, str] = field(default_factory=dict)
    analytics: Mapping[str, Any] | None = None
    # Analytics speaker key -> hex colour.
    analytics_colors: Mapping[str, str] = field(default_factory=dict)
    # The reader's timeline notes, placed among the transcript's lines.
    timeline_notes: Sequence[TimelineNoteLike] = ()
    # The reader's captures, placed among the lines as notes are.
    captures: Sequence[ExportCapture] = ()
    # False leaves the reader's own notes out of the whole file: the timeline
    # notes and captures above and the ``## User Notes`` section that ends
    # generated notes. The stored notes are not changed.
    include_user_notes: bool = True


def ordered_sections(requested: Sequence[str]) -> tuple[str, ...]:
    wanted = set(requested)
    return tuple(key for key in SECTION_ORDER if key in wanted)


def _absent(text: str) -> tuple[Block, ...]:
    return (Callout(tone="neutral", title=None, body=(Paragraph(plain(text)),)),)


def _notes_section(parts: MeetingParts) -> Section:
    notes = parts.notes or ""
    if not parts.include_user_notes:
        notes = split_user_notes_section(notes)[0].rstrip()
    if not notes.strip():
        return Section(
            "notes",
            SECTION_TITLES["notes"],
            _absent("No meeting notes have been generated for this meeting."),
        )
    return Section(
        "notes",
        SECTION_TITLES["notes"],
        notes_to_blocks(notes),
        markdown_source=notes,
    )


# What a note on selected words shows of them.
QUOTE_MAX_CHARS = 160


def _quote(text: str | None) -> str:
    quote = " ".join((text or "").split())
    if len(quote) > QUOTE_MAX_CHARS:
        return quote[: QUOTE_MAX_CHARS - 1].rstrip() + "…"
    return quote


def _note_block(placement: NotePlacement[TimelineNoteLike]) -> Paragraph:
    """A timeline note as a quoted paragraph, which every format sets apart.

    A note at a time is one line, stamped like the transcript's own lines, so a
    body that holds "\n[12:00] Bob: approve" can't pass for a line of speech.
    A note on the whole meeting keeps the user's line breaks; plain text quotes
    each of them with "> ".
    """
    note = placement.note
    if placement.slot == "head":
        label: tuple[Span, ...] = (Span("Note before the transcript", bold=True),)
    elif placement.slot == "tail":
        label = (Span("Note after the transcript", bold=True),)
    else:
        selection = note.kind == TimelineNoteKind.SELECTION.value
        start_ms = (
            note.start_ms
            if selection and note.start_ms is not None
            else note_time_ms(note)
        )
        label = (
            Span(format_transcript_time(start_ms / 1000) + " "),
            Span("Note", bold=True),
        )
        if selection:
            label += (Span(f" on “{_quote(note.quoted_text)}”", italic=True),)
        body = " / ".join(
            part.strip() for part in note.body.splitlines() if part.strip()
        )
        return Paragraph((*label, Span(": " + body)), tone="quote")
    lines = [line.strip() for line in note.body.splitlines() if line.strip()]
    spans: list[Span] = [*label, Span(":")]
    for line in lines:
        spans += [LINE_BREAK, Span(line)]
    return Paragraph(tuple(spans), tone="quote")


def _capture_block(capture: ExportCapture) -> Block:
    """A capture as a picture under a stamped caption, like a note at a time.

    Plain text says where the picture was, since it cannot show it.
    """
    caption: tuple[Span, ...] = (
        Span(format_transcript_time(capture.anchor_ms / 1000) + " "),
        Span("Capture", bold=True),
    )
    if capture.image is None:
        return Paragraph(
            (*caption, Span(": the image could not be read")), tone="quote"
        )
    return ImageBlock(
        caption=caption,
        data=capture.image.data,
        width_px=capture.image.width_px,
        height_px=capture.image.height_px,
        alt_text=capture.title,
        fallback=(
            Paragraph(
                (*caption, Span(": an image, in the PDF and Word exports")),
                tone="quote",
            ),
        ),
    )


def _timeline_block(placement: NotePlacement[Any]) -> Block:
    if isinstance(placement.note, ExportCapture):
        return _capture_block(placement.note)
    return _note_block(placement)


def _transcript_section(parts: MeetingParts) -> Section:
    lines = tuple(
        TranscriptLine(
            start_seconds=float(segment.get("start") or 0),
            speaker=speaker_display_name(label, parts.speaker_names_by_label),
            color=parts.speaker_colors_by_label.get(label),
            text=str(segment.get("text") or "").strip(),
        )
        for segment in parts.segments
        for label in (str(segment.get("speaker") or "Unknown"),)
    )
    # Placed on the same lines as the transcript view, so each note follows
    # the line it follows there.
    placed: list[Any] = (
        [*parts.timeline_notes, *parts.captures] if parts.include_user_notes else []
    )
    placements = place_timeline_notes(parts.segments, placed)
    blocks: tuple[Block, ...] = tuple(
        lines[item] if isinstance(item, int) else _timeline_block(item)
        for item in iter_timeline(len(lines), placements)
    )
    if not lines:
        blocks = _absent("This meeting has no transcript text.") + blocks
    return Section("transcript", SECTION_TITLES["transcript"], blocks)


def _analytics_section(parts: MeetingParts) -> Section:
    return Section(
        "analytics",
        SECTION_TITLES["analytics"],
        analytics_blocks(parts.analytics, parts.analytics_colors),
    )


_BUILDERS = {
    "notes": _notes_section,
    "analytics": _analytics_section,
    "transcript": _transcript_section,
}


def _meta(parts: MeetingParts) -> tuple[MetaItem, ...]:
    duration = (
        format_duration(parts.duration_seconds * 1000)
        if parts.duration_seconds
        else "Unknown"
    )
    items = [
        MetaItem(
            "Date", format_meeting_datetime(parts.created_at, parts.timezone_name)
        ),
        MetaItem("Duration", duration),
    ]
    if parts.speaker_names:
        items.append(MetaItem("Speakers", ", ".join(parts.speaker_names)))
    return tuple(items)


def build_document(
    parts: MeetingParts, sections: Sequence[str], *, branded: bool = True
) -> ExportDocument:
    return ExportDocument(
        title=parts.title,
        meta=_meta(parts),
        sections=tuple(_BUILDERS[key](parts) for key in ordered_sections(sections)),
        dated=format_meeting_date(parts.created_at, parts.timezone_name),
        branded=branded,
        speakers=parts.speaker_names,
    )
