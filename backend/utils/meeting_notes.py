import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from backend.prompts import render_prompt
from backend.utils.live_speaker_labels import speaker_display_name

PLACEHOLDER_SPEAKER_PATTERN = re.compile(
    r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$",
    re.IGNORECASE,
)

# The meeting-notes body is assembled from four parts, three of which are
# protected and one of which the user may replace with their own structure
# (issue #137). Both note-generation paths assemble it the same way -- the
# unified meeting-intelligence prompt (inside its JSON ``notes_markdown`` field)
# and the standalone regeneration prompt (as raw Markdown) -- so the two can
# never drift in structure or quality. A test asserts both templates embed the
# assembled default.
#
# Protected parts carry the contracts the rest of the application depends on:
# the preamble keeps the notes free of a title heading (the UI renders the title
# separately) and starting at a section heading; the table rules are what make
# the notes render as real tables in the editor; the fidelity rules are the
# anti-hallucination and attribution guarantees. A user-supplied structure is
# sandwiched between them and can therefore change what the notes contain, never
# how faithful or how parseable they are.
#
# Nothing here is a format template: prompts are composed by concatenation (see
# backend.utils.prompt_blocks), so braces, backslashes and Markdown in a
# user-authored structure are just characters and need no escaping.
NOTES_SPEC_PREAMBLE = render_prompt("notes.preamble")

# The editable half. Bump NOTES_SECTIONS_VERSION whenever this changes so
# templates forked from an older wording can tell the user their copy is stale.
NOTES_SECTIONS_VERSION = 2

DEFAULT_NOTES_SECTIONS = render_prompt("notes.default_sections")

NOTES_TABLE_RULES = render_prompt("notes.table_rules")

NOTES_FIDELITY_RULES = render_prompt("notes.fidelity_rules")


def build_notes_body_spec(notes_sections: Optional[str] = None) -> str:
    """Assemble the notes body spec around a section structure.

    ``notes_sections`` of ``None`` or blank yields the shipped default, so every
    caller that has no custom template renders a byte-identical prompt to the
    one used before this feature existed.
    """
    sections = (notes_sections or "").strip() or DEFAULT_NOTES_SECTIONS
    return "\n\n".join(
        [
            NOTES_SPEC_PREAMBLE,
            sections,
            NOTES_TABLE_RULES,
            NOTES_FIDELITY_RULES,
        ]
    )


# The assembled default, kept as a module constant because both default prompt
# templates embed it and a test asserts they stay in step.
NOTES_BODY_SPEC = build_notes_body_spec()


_LEADING_TITLE_HEADING_PATTERN = re.compile(r"^\s*#\s+[^\n]*(?:\n+|\Z)")


def strip_leading_title_heading(notes_markdown: str) -> str:
    """Remove a single leading level-1 (``# ...``) title line from notes.

    Generated notes must begin at the Summary section: the application renders the
    meeting title separately, so a title heading inside the notes is redundant and
    can even contradict it. The prompt instructs the model accordingly; this
    defensively strips a title heading if a model still emits one. Level-2+
    headings (``## ...``) are left untouched.
    """
    if not notes_markdown:
        return notes_markdown
    return _LEADING_TITLE_HEADING_PATTERN.sub("", notes_markdown, count=1).strip()


def resolve_recording_speaker_name(speaker: Any) -> Optional[str]:
    resolved_name = (
        getattr(speaker, "local_name", None)
        or getattr(getattr(speaker, "global_speaker", None), "name", None)
        or getattr(speaker, "name", None)
        or getattr(speaker, "diarization_label", None)
    )
    if resolved_name is None:
        return None

    cleaned_name = str(resolved_name).strip()
    return cleaned_name or None


def is_placeholder_speaker_name(name: Optional[str]) -> bool:
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        return True
    return bool(PLACEHOLDER_SPEAKER_PATTERN.match(cleaned_name))


def build_recording_speaker_map(speakers: Iterable[Any]) -> Dict[str, str]:
    speaker_map: Dict[str, str] = {}

    for speaker in speakers:
        name = resolve_recording_speaker_name(speaker)
        label = getattr(speaker, "diarization_label", None)
        if label and name:
            speaker_map[label] = name

    return speaker_map


def _format_prompt_timestamp(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def format_segments_for_llm(
    segments: Iterable[dict],
    speaker_map: Dict[str, str],
    *,
    with_end: bool = True,
) -> str:
    """One ``[MM:SS - MM:SS] Name (with ...): text`` line per segment.

    ``with_end=False`` drops the end time (``[MM:SS] Name: text``). Chat sends
    the whole transcript on every turn and asks the model to cite start
    times, so the end time there costs tokens and adds little: it is almost
    always the next line's start.
    """
    lines = []

    for segment in segments:
        speaker_label = segment.get("speaker", "Unknown")
        speaker_name = speaker_display_name(speaker_label, speaker_map)
        start_seconds = float(segment.get("start", 0))
        timestamp = _format_prompt_timestamp(start_seconds)
        if with_end:
            end_seconds = float(segment.get("end", start_seconds))
            timestamp = f"{timestamp} - {_format_prompt_timestamp(end_seconds)}"
        overlapping = segment.get("overlapping_speakers") or []
        overlapping_names = [
            speaker_display_name(label, speaker_map) for label in overlapping
        ]
        overlapping_suffix = (
            f" (with {', '.join(overlapping_names)})" if overlapping_names else ""
        )
        text = str(segment.get("text", "")).strip()
        lines.append(f"[{timestamp}] {speaker_name}{overlapping_suffix}: {text}")

    return "\n".join(lines)


def build_user_notes_prompt_section(user_notes: Optional[str]) -> str:
    """The ``# User Notes Context`` section.

    ``user_notes`` is the section already written from the recording's timeline
    notes (``RecordingTimelineNotes.user_notes_context`` beside a transcript
    that carries them, ``user_notes_list`` beside one that does not).
    """
    cleaned_notes = (user_notes or "").strip()
    if not cleaned_notes:
        return render_prompt("shared.no_user_notes")
    return cleaned_notes


@dataclass
class MeetingEventContext:
    """Lightweight value object describing the calendar event a recording is
    linked to. Threaded through the three LLM prompt paths so notes generation
    and speaker inference can use the meeting's agenda and attendee list.
    """

    title: Optional[str] = None
    description: Optional[str] = None
    attendees: List[str] = field(default_factory=list)


def meeting_event_context_from_calendar_event(
    event: Any,
) -> Optional[MeetingEventContext]:
    """Build a :class:`MeetingEventContext` from a ``CalendarEvent`` model.

    Returns ``None`` when no event is supplied so the prompt paths fall back
    to the unchanged "no context" string.
    """
    if event is None:
        return None
    raw_attendees = getattr(event, "attendees", None) or []
    attendee_names: List[str] = []
    for attendee in raw_attendees:
        if isinstance(attendee, dict):
            name = attendee.get("name") or attendee.get("email")
        else:
            name = attendee
        if name:
            cleaned = str(name).strip()
            if cleaned:
                attendee_names.append(cleaned)
    return MeetingEventContext(
        title=getattr(event, "title", None),
        description=getattr(event, "description", None),
        attendees=attendee_names,
    )


@dataclass
class AttachedDocument:
    """One parsed document, ready to be rendered into a prompt."""

    title: str
    text: str
    truncated: bool = False
    # How the text was produced, in the model's terms. Stated in the prompt so
    # a model reading a vision transcription does not describe the document as
    # text-only -- true of what it received, wrong about the document.
    extracted_by: Optional[str] = None
    # A note's file: the note's time on the audio timeline, the clock of the
    # transcript's own timestamps, or None for a note on the whole meeting.
    note_at_ms: Optional[int] = None
    attached_to_note: bool = False


# Document text is untrusted input. It is uploaded by the user, but its contents
# are authored by whoever produced the file, and visual parsing widens that
# further: a model transcribing a page will faithfully reproduce any instruction
# printed on it. Both sinks matter -- notes generation writes the notes document,
# and meeting chat holds an update_meeting_notes tool that can overwrite it.
#
# The delimiters plus an explicit data-not-instructions rule are the standard
# mitigation. They are not airtight, but they raise the bar substantially and
# they cost nothing.
_DOCUMENT_SECTION_PREAMBLE = render_prompt("shared.documents_preamble")


# Added only when the user switched a note's file on, so every prompt without
# one stays as it was. The notes are Markdown text and a file is opened from
# the app, so the model refers to it rather than embedding it.
_NOTE_FILE_SECTION_NOTE = render_prompt("shared.note_file_explanation")


def build_documents_prompt_section(
    documents: Optional[Sequence[AttachedDocument]],
) -> str:
    """Render the attached-documents block, or a fixed fallback when there are none."""
    usable = [
        document
        for document in (documents or [])
        if document.text and document.text.strip()
    ]
    if not usable:
        return render_prompt("shared.no_documents")

    blocks: List[str] = [_DOCUMENT_SECTION_PREAMBLE]
    if any(document.attached_to_note for document in usable):
        blocks.append(_NOTE_FILE_SECTION_NOTE)
    for document in usable:
        note = render_prompt("shared.document_truncated") if document.truncated else ""
        origin = (
            f' extracted_by="{escape_prompt_attribute(document.extracted_by)}"'
            if document.extracted_by
            else ""
        )
        if document.attached_to_note:
            at = document.note_at_ms
            stamp = "meeting" if at is None else _format_prompt_timestamp(at / 1000)
            origin += f' note_at="{stamp}"'
        blocks.append(
            f'<attached_document title="{escape_prompt_attribute(document.title)}"'
            f"{origin}{note}>\n"
            f"{document.text.strip()}\n"
            "</attached_document>"
        )
    return "\n\n".join(blocks)


def escape_prompt_attribute(value: str) -> str:
    """Keep a document title from closing the tag that quotes it."""
    return (value or "").replace('"', "'").replace("<", "").replace(">", "")


def build_meeting_context_prompt_section(
    event_context: Optional[MeetingEventContext],
) -> str:
    """Render the ``{meeting_context_section}`` block for the LLM prompts.

    With no linked event this returns a fixed fallback string, leaving the
    rendered prompt unchanged in substance. With an event it provides the
    title and description as agenda context and the attendee names as
    *candidate* speaker names.
    """
    if event_context is None:
        return render_prompt("shared.no_calendar_event")

    lines: List[str] = []
    title = (event_context.title or "").strip()
    if title:
        lines.append(render_prompt("shared.calendar_title", title=title))

    description = (event_context.description or "").strip()
    if description:
        lines.append(
            render_prompt("shared.calendar_description", description=description)
        )

    attendees = [name for name in event_context.attendees if name and name.strip()]
    if attendees:
        attendee_list = ", ".join(attendees)
        lines.append(
            render_prompt("shared.calendar_attendees", attendee_list=attendee_list)
        )

    if not lines:
        return render_prompt("shared.no_calendar_event")

    return "\n\n".join(lines)


@dataclass
class MeetingMetadata:
    """Facts about the recording itself, injected into the notes prompt.

    A user-editable structure cannot carry placeholders (issue #137), so these
    are always supplied instead: a structure that says "open with the date and
    attendees" then has the facts available to do it.
    """

    title: Optional[str] = None
    recorded_on: Optional[str] = None
    duration: Optional[str] = None
    participants: List[str] = field(default_factory=list)


def format_duration_for_prompt(duration_seconds: Optional[float]) -> Optional[str]:
    """Render a duration as human prose, or ``None`` when it is unknown."""
    if duration_seconds is None:
        return None
    try:
        total_seconds = int(float(duration_seconds))
    except (TypeError, ValueError):
        return None
    if total_seconds <= 0:
        return None

    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    parts: List[str] = []
    if hours:
        parts.append(
            render_prompt(
                "shared.duration_hours",
                hours=str(hours),
                plural_suffix="s" if hours != 1 else "",
            )
        )
    if minutes:
        parts.append(
            render_prompt(
                "shared.duration_minutes",
                minutes=str(minutes),
                plural_suffix="s" if minutes != 1 else "",
            )
        )
    if not parts:
        return render_prompt("shared.duration_under_minute")
    return " ".join(parts)


def build_meeting_metadata_prompt_section(
    metadata: Optional[MeetingMetadata],
) -> str:
    """Render the ``{meeting_metadata_section}`` block for the notes prompts."""
    if metadata is None:
        return render_prompt("shared.no_metadata")

    lines: List[str] = []
    title = (metadata.title or "").strip()
    if title:
        lines.append(render_prompt("shared.recording_title", title=title))

    recorded_on = (metadata.recorded_on or "").strip()
    if recorded_on:
        lines.append(render_prompt("shared.recorded_on", recorded_on=recorded_on))

    duration = (metadata.duration or "").strip()
    if duration:
        lines.append(render_prompt("shared.duration", duration=duration))

    participants = [name.strip() for name in metadata.participants if name.strip()]
    if participants:
        lines.append(
            render_prompt("shared.participants", participants=", ".join(participants))
        )

    if not lines:
        return render_prompt("shared.no_metadata")

    return "\n".join(lines)


@dataclass(frozen=True)
class NotesPromptContext:
    """Everything issue #137 adds to a notes prompt, in one argument.

    Bundled rather than passed as parallel keyword arguments because the same
    set has to cross five LLM backends, the factory and the CLI backend, and a
    bundle keeps those signatures stable as members are added -- which is
    exactly what ``documents`` did. ``None`` everywhere reproduces the
    pre-feature prompt exactly.
    """

    notes_sections: Optional[str] = None
    glossary: Optional[str] = None
    metadata: Optional["MeetingMetadata"] = None
    # Parsed text of the meeting's attached documents. Uncapped by design,
    # matching the transcript, which has never been truncated either.
    documents: Optional[Sequence["AttachedDocument"]] = None


def build_glossary_prompt_section(
    glossary: Optional[str],
    *,
    for_notes: bool = True,
) -> str:
    """Render the ``{glossary_section}`` block from already-merged glossary text.

    Merging the install-wide and personal lists happens upstream in
    ``backend.utils.notes_templates``; this only renders the result. The two
    consumers want opposite things from it: notes must not grow a glossary
    section of their own, while Meeting Edge exists to explain terms and should
    prefer these definitions when it does.
    """
    cleaned = (glossary or "").strip()
    if not cleaned:
        return render_prompt("shared.no_glossary")

    if for_notes:
        usage = render_prompt("shared.glossary_notes_usage")
    else:
        usage = render_prompt("shared.glossary_edge_usage")

    return render_prompt("shared.glossary", usage=usage, cleaned=cleaned)


# The section append_user_notes_section writes: everything from its heading to
# the end of the notes.
# The heading alone on its line, wherever it starts, to the end of the notes.
USER_NOTES_SECTION_PATTERN = re.compile(
    r"^##[ \t]+User Notes[ \t]*$[\s\S]*\Z", re.IGNORECASE | re.MULTILINE
)
_LIST_MARKER_PATTERN = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")


def split_user_notes_section(notes: str) -> tuple[str, str]:
    """Generated notes and the ``## User Notes`` section after them, if any."""
    match = USER_NOTES_SECTION_PATTERN.search(notes)
    if match is None:
        return notes, ""
    return notes[: match.start()], notes[match.start() :]


def append_user_notes_section(notes: str, note_lines: Sequence[str]) -> str:
    """Generated notes with the user's own notes listed after them.

    ``note_lines`` come from ``RecordingTimelineNotes.appendix_lines``, in
    timeline order. A section the model wrote itself is replaced, so the list
    is always the user's words.
    """
    notes_without_user_section, _ = split_user_notes_section(notes.rstrip())

    bullet_lines = []
    for raw_line in note_lines:
        line = _LIST_MARKER_PATTERN.sub("", raw_line.strip())
        if line:
            bullet_lines.append(f"- [User] {line}")

    if not bullet_lines:
        return notes_without_user_section.strip()

    return f"{notes_without_user_section.rstrip()}\n\n## User Notes\n" + "\n".join(
        bullet_lines
    )
