import re
from typing import Any, Dict, Iterable, Optional


def build_recording_speaker_map(speakers: Iterable[Any]) -> Dict[str, str]:
    speaker_map: Dict[str, str] = {}

    for speaker in speakers:
        name = (
            getattr(speaker, "local_name", None)
            or getattr(getattr(speaker, "global_speaker", None), "name", None)
            or getattr(speaker, "name", None)
            or getattr(speaker, "diarization_label", None)
        )
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