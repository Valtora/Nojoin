import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


PLACEHOLDER_SPEAKER_PATTERN = re.compile(
    r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$",
    re.IGNORECASE,
)


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


def meeting_event_context_from_calendar_event(event: Any) -> Optional[MeetingEventContext]:
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

    return f"{notes_without_user_section.rstrip()}\n\n## User Notes\n" + "\n".join(bullet_lines)