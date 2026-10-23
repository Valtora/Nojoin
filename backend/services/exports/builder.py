"""Assemble an export document from a meeting's parts.

The order is fixed whatever the reader ticked: notes, then analytics, then the
transcript. Notes are what most readers came for, analytics qualify how the
meeting went, and the transcript is the evidence for both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.utils.live_speaker_labels import speaker_display_name

from .analytics_blocks import analytics_blocks
from .formatting import format_duration, format_meeting_date, format_meeting_datetime
from .markdown_blocks import notes_to_blocks
from .model import (
    Block,
    Callout,
    ExportDocument,
    MetaItem,
    Paragraph,
    Section,
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


def ordered_sections(requested: Sequence[str]) -> tuple[str, ...]:
    wanted = set(requested)
    return tuple(key for key in SECTION_ORDER if key in wanted)


def _absent(text: str) -> tuple[Block, ...]:
    return (Callout(tone="neutral", title=None, body=(Paragraph(plain(text)),)),)


def _notes_section(parts: MeetingParts) -> Section:
    if not parts.notes or not parts.notes.strip():
        return Section(
            "notes",
            SECTION_TITLES["notes"],
            _absent("No meeting notes have been generated for this meeting."),
        )
    return Section(
        "notes",
        SECTION_TITLES["notes"],
        notes_to_blocks(parts.notes),
        markdown_source=parts.notes,
    )


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
    blocks: tuple[Block, ...] = lines or _absent("This meeting has no transcript text.")
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
