import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

PLACEHOLDER_SPEAKER_PATTERN = re.compile(
    r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$",
    re.IGNORECASE,
)

# Single source of truth for the meeting-notes body: the section structure and
# fidelity rules the model must follow. Both note-generation paths embed this
# verbatim -- the unified meeting-intelligence prompt (inside its JSON
# ``notes_markdown`` field) and the standalone regeneration prompt (as raw
# Markdown) -- so the two can never drift in structure or quality again. A test
# asserts both templates contain it.
#
# The layout follows external best practice: lead with a scannable summary,
# surface decisions and action items where they drive follow-through, then the
# per-topic detail. Keep this string free of ``{``/``}`` so it survives the
# ``str.format`` call each embedding template still runs.
NOTES_BODY_SPEC = """Structure the notes for fast follow-through, not verbatim transcription. Write Markdown and begin directly with the Summary section below. Do not add a title heading of your own; the application already displays the meeting title above the notes. Translate every heading below into the requested output language, keeping this order and structure.

## Summary
Two to four sentences the reader can scan first: why the meeting happened and what came out of it. Lead with outcomes, not chronology.

## Key Decisions
Each decision the meeting actually reached, one per line, paired with the reasoning behind it so it is not re-litigated later:
- The decision - the reasoning or trade-off behind it.
Omit this entire section if no decisions were reached. Never record a decision that was not made.

## Action Items / Tasks
Every task, commitment, or follow-up that was actually raised, one per line:
- [ ] Task description - Owner: single named person, or Unassigned - Due: a date, or TBD
Give each item exactly one owner; never assign a task to the team as a whole. If none were raised, write a single line stating that no action items were identified.

## Detailed Notes
One ### subsection per major topic, in the order discussed, with the subheading naming the topic. Under each, include only what applies:
- Key Points: the substantive information, arguments, or figures, attributed to the participant who raised them where that matters.
- Discussion: what was debated, including differing perspectives, summarised rather than transcribed.
- Open Questions: anything left unresolved or needing follow-up.

## Miscellaneous
Anything material that fits nowhere above: announcements, FYIs, or references to external documents and prior meetings. If nothing applies, write a single line saying so.

Apply these fidelity rules throughout:
- Be comprehensive about what was said, but favour signal over volume: capture every decision, commitment, and material point, and leave out greetings, small talk, and filler.
- Never invent facts, decisions, action items, figures, or attributions. If it was not said, do not record it.
- Attribute claims and commitments to the participant who made them, using their mapped name or role rather than a generic label.
- Weave any user-authored notes into the relevant sections where they improve accuracy. The application preserves the raw user notes separately, so do not add your own appendix for them and do not label content as AI-generated or user-generated."""


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


def format_segments_for_llm(
    segments: Iterable[dict],
    speaker_map: Dict[str, str],
) -> str:
    lines = []

    for segment in segments:
        speaker_label = segment.get("speaker", "Unknown")
        speaker_name = speaker_map.get(speaker_label, speaker_label)
        start_seconds = float(segment.get("start", 0))
        end_seconds = float(segment.get("end", start_seconds))
        start_minutes = int(start_seconds // 60)
        start_remainder = int(start_seconds % 60)
        end_minutes = int(end_seconds // 60)
        end_remainder = int(end_seconds % 60)
        overlapping = segment.get("overlapping_speakers") or []
        overlapping_names = [speaker_map.get(label, label) for label in overlapping]
        overlapping_suffix = (
            f" (with {', '.join(overlapping_names)})" if overlapping_names else ""
        )
        text = str(segment.get("text", "")).strip()
        lines.append(
            f"[{start_minutes:02d}:{start_remainder:02d} - {end_minutes:02d}:{end_remainder:02d}] "
            f"{speaker_name}{overlapping_suffix}: {text}"
        )

    return "\n".join(lines)


def build_user_notes_prompt_section(user_notes: Optional[str]) -> str:
    cleaned_notes = (user_notes or "").strip()
    if not cleaned_notes:
        return "No user-authored notes were provided for this meeting."

    return (
        "The user recorded the following manual notes while waiting for the meeting to finish processing. "
        "Use them as high-priority supporting context when composing the final notes. "
        "Incorporate relevant items into the summary, detailed notes, and action items where they materially improve accuracy.\n\n"
        f"{cleaned_notes}"
    )


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
        return "No calendar event is linked to this meeting."

    lines: List[str] = []
    title = (event_context.title or "").strip()
    if title:
        lines.append(f"Meeting title: {title}")

    description = (event_context.description or "").strip()
    if description:
        lines.append(f"Agenda / description:\n{description}")

    attendees = [name for name in event_context.attendees if name and name.strip()]
    if attendees:
        attendee_list = ", ".join(attendees)
        lines.append(
            "Invited attendees (candidate speaker names): "
            f"{attendee_list}. "
            "Prefer one of these names when a diarization label's real name is "
            "unclear and the transcript supports it; never invent a name that is "
            "neither in the transcript nor an attendee."
        )

    if not lines:
        return "No calendar event is linked to this meeting."

    return "\n\n".join(lines)


def append_user_notes_section(notes: str, user_notes: Optional[str]) -> str:
    cleaned_notes = (user_notes or "").strip()
    if not cleaned_notes:
        return notes.strip()

    notes_without_user_section = re.sub(
        r"\n##\s+User Notes\b[\s\S]*$",
        "",
        notes.rstrip(),
        flags=re.IGNORECASE,
    )

    bullet_lines = []
    for raw_line in cleaned_notes.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+|\[[^\]]+\]\s+)", "", line)
        bullet_lines.append(f"- [User] {line}")

    if not bullet_lines:
        return notes_without_user_section.strip()

    return f"{notes_without_user_section.rstrip()}\n\n## User Notes\n" + "\n".join(
        bullet_lines
    )
