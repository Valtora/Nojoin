from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from backend.utils.meeting_notes import (
    MeetingEventContext,
    append_user_notes_section,
    build_meeting_context_prompt_section,
    build_user_notes_prompt_section,
    is_placeholder_speaker_name,
    resolve_recording_speaker_name,
)


JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

DEFAULT_AUTOMATIC_MEETING_INTELLIGENCE_PROMPT_TEMPLATE = """You are an expert meeting intelligence assistant.

Your task is to produce one valid JSON object that combines:
1. speaker suggestions for unresolved diarization labels only
2. a meeting title
3. final meeting notes in Markdown

# Critical Rules
- Treat any non-generic speaker names already present in the transcript as trusted.
- Only use `speaker_mapping` for the unresolved labels listed below.
- If you are not confident about a label, omit it from `speaker_mapping` and keep the generic label in `notes_markdown`.
- The names or roles used in `notes_markdown` for unresolved labels must match the returned `speaker_mapping` entries exactly.
- The `title` and `notes_markdown` must reflect the same meeting interpretation.
- Return valid JSON only. Do not include prose before or after the JSON object.
- Escape newlines inside `notes_markdown` according to JSON rules.

# Title Style
{title_preference_instruction}

# Unresolved Speaker Labels
{unresolved_speakers_section}

# User Notes Context
{user_notes_section}

# Meeting Context
{meeting_context_section}

# Required JSON Schema
{{
    "speaker_mapping": {{
        "SPEAKER_00": "Person name or role"
    }},
    "title": "Meeting title",
    "notes_markdown": "# Meeting Notes\\n\\n## Topics Discussed\\n..."
}}

# Notes Markdown Requirements
- Use Markdown.
- Start with `# Meeting Notes`.
- Use this exact section order:
    1. `## Topics Discussed`
    2. `## Summary`
    3. `## Detailed Notes`
    4. `## Action Items / Tasks`
    5. `## Miscellaneous`
- Incorporate relevant user-authored notes into the body where they materially improve accuracy.
- Do not add a separate appendix for user notes.

# Transcript
{transcript}
"""


class MeetingIntelligenceContractError(ValueError):
    """Raised when a unified meeting-intelligence payload breaks the contract."""


class AutomaticMeetingIntelligenceFailurePolicy(str, Enum):
    """Controls how the worker should react to unified AI contract failures."""

    FAIL_CLOSED = "fail_closed"


DEFAULT_AUTOMATIC_MEETING_INTELLIGENCE_FAILURE_POLICY = (
    AutomaticMeetingIntelligenceFailurePolicy.FAIL_CLOSED
)


@dataclass(frozen=True)
class AutomaticMeetingIntelligenceRequest:
    """Contract for the automatic unified meeting-intelligence call.

    The request must be built after deterministic speaker resolution has already
    preserved trusted names. The transcript should therefore contain the current
    post-resolution state, while unresolved diarization labels remain visible.
    """

    resolved_transcript: str
    unresolved_speakers: tuple[str, ...]
    user_notes: str | None = None
    prefer_short_titles: bool = True
    meeting_context: MeetingEventContext | None = None

    def __post_init__(self) -> None:
        transcript = self.resolved_transcript.strip()
        if not transcript:
            raise MeetingIntelligenceContractError(
                "resolved_transcript must be a non-empty string"
            )

        normalized_labels = tuple(
            str(label).strip() for label in self.unresolved_speakers if str(label).strip()
        )
        if len(set(normalized_labels)) != len(normalized_labels):
            raise MeetingIntelligenceContractError(
                "unresolved_speakers must not contain duplicates"
            )

        normalized_user_notes = self.user_notes.strip() if self.user_notes else None

        object.__setattr__(self, "resolved_transcript", transcript)
        object.__setattr__(self, "unresolved_speakers", normalized_labels)
        object.__setattr__(self, "user_notes", normalized_user_notes or None)

    @property
    def has_unresolved_speakers(self) -> bool:
        return bool(self.unresolved_speakers)


@dataclass(frozen=True)
class AutomaticMeetingIntelligenceResult:
    """Normalized result for the automatic unified meeting-intelligence call."""

    speaker_mapping: dict[str, str]
    title: str
    notes_markdown: str

    def __post_init__(self) -> None:
        normalized_mapping = {
            str(label).strip(): str(name).strip()
            for label, name in self.speaker_mapping.items()
            if str(label).strip() and str(name).strip()
        }
        title = re.sub(r"\s+", " ", self.title.strip())
        notes_markdown = self.notes_markdown.replace("\r\n", "\n").strip()

        if not title:
            raise MeetingIntelligenceContractError("title must be a non-empty string")

        if not notes_markdown:
            raise MeetingIntelligenceContractError(
                "notes_markdown must be a non-empty string"
            )

        if not notes_markdown.startswith("# Meeting Notes"):
            raise MeetingIntelligenceContractError(
                "notes_markdown must start with '# Meeting Notes'"
            )

        object.__setattr__(self, "speaker_mapping", normalized_mapping)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "notes_markdown", notes_markdown)

    def validate_for_request(
        self,
        request: AutomaticMeetingIntelligenceRequest,
    ) -> None:
        unknown_labels = sorted(
            label for label in self.speaker_mapping if label not in request.unresolved_speakers
        )
        if unknown_labels:
            raise MeetingIntelligenceContractError(
                "speaker_mapping contains labels that were not unresolved in the request: "
                + ", ".join(unknown_labels)
            )


def get_default_automatic_meeting_intelligence_prompt_template() -> str:
    return DEFAULT_AUTOMATIC_MEETING_INTELLIGENCE_PROMPT_TEMPLATE


def build_automatic_meeting_intelligence_request(
    resolved_transcript: str,
    speakers: Iterable[Any],
    *,
    user_notes: str | None = None,
    prefer_short_titles: bool = True,
    meeting_context: MeetingEventContext | None = None,
) -> AutomaticMeetingIntelligenceRequest:
    return AutomaticMeetingIntelligenceRequest(
        resolved_transcript=resolved_transcript,
        unresolved_speakers=get_speakers_eligible_for_llm_renaming(speakers),
        user_notes=user_notes,
        prefer_short_titles=prefer_short_titles,
        meeting_context=meeting_context,
    )


def get_speakers_eligible_for_llm_renaming(
    speakers: Iterable[Any],
) -> tuple[str, ...]:
    labels: list[str] = []

    for speaker in speakers:
        label = str(getattr(speaker, "diarization_label", "")).strip()
        if not label:
            continue
        if getattr(speaker, "merged_into_id", None):
            continue
        if getattr(speaker, "local_name", None):
            continue
        if getattr(speaker, "global_speaker_id", None) or getattr(
            speaker, "global_speaker", None
        ):
            continue

        resolved_name = resolve_recording_speaker_name(speaker)
        if not is_placeholder_speaker_name(resolved_name):
            continue

        labels.append(label)

    return tuple(dict.fromkeys(labels))


def build_automatic_meeting_intelligence_prompt(
    request: AutomaticMeetingIntelligenceRequest,
    prompt_template: str | None = None,
) -> str:
    template = (
        prompt_template or get_default_automatic_meeting_intelligence_prompt_template()
    )
    return template.format(
        transcript=request.resolved_transcript,
        unresolved_speakers_section=build_unresolved_speakers_prompt_section(
            request.unresolved_speakers
        ),
        user_notes_section=build_user_notes_prompt_section(request.user_notes),
        meeting_context_section=build_meeting_context_prompt_section(
            request.meeting_context
        ),
        title_preference_instruction=build_title_preference_instruction(
            request.prefer_short_titles
        ),
    )


def finalise_automatic_meeting_intelligence_result(
    result: AutomaticMeetingIntelligenceResult,
    user_notes: str | None,
) -> AutomaticMeetingIntelligenceResult:
    return AutomaticMeetingIntelligenceResult(
        speaker_mapping=result.speaker_mapping,
        title=result.title,
        notes_markdown=append_user_notes_section(result.notes_markdown, user_notes),
    )


def build_title_preference_instruction(prefer_short_titles: bool) -> str:
    if prefer_short_titles:
        return (
            "Prefer a short, punchy title of 3-5 words. Keep it concise while still "
            "describing the meeting clearly."
        )

    return "Provide a concise descriptive title of at most 12 words."


def build_unresolved_speakers_prompt_section(unresolved_speakers: Sequence[str]) -> str:
    labels = [str(label).strip() for label in unresolved_speakers if str(label).strip()]
    if not labels:
        return "No unresolved speaker labels remain. Return an empty object for `speaker_mapping`."

    lines = ["Only these diarization labels may appear in `speaker_mapping`:"]
    lines.extend(f"- {label}" for label in labels)
    return "\n".join(lines)


def parse_automatic_meeting_intelligence_response(
    response_text: str,
    *,
    request: AutomaticMeetingIntelligenceRequest | None = None,
) -> AutomaticMeetingIntelligenceResult:
    payload = _load_meeting_intelligence_payload(response_text)
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping=_read_speaker_mapping(payload),
        title=_read_required_string(payload, "title"),
        notes_markdown=_read_required_string(payload, "notes_markdown"),
    )

    if request is not None:
        result.validate_for_request(request)

    return result


def _load_meeting_intelligence_payload(response_text: str) -> Mapping[str, Any]:
    text = response_text.strip()
    if not text:
        raise MeetingIntelligenceContractError("response_text must be a non-empty string")

    direct_payload = _try_load_json_object(text)
    if direct_payload is not None:
        return direct_payload

    for match in JSON_FENCE_PATTERN.finditer(text):
        fenced_payload = _try_load_json_object(match.group(1).strip())
        if fenced_payload is not None:
            return fenced_payload

    inline_payload = _try_extract_inline_json_object(text)
    if inline_payload is not None:
        return inline_payload

    raise MeetingIntelligenceContractError(
        "Could not parse a unified meeting-intelligence JSON object from the response"
    )


def _try_load_json_object(candidate: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        raise MeetingIntelligenceContractError(
            "Unified meeting-intelligence response must be a JSON object"
        )

    return payload


def _try_extract_inline_json_object(text: str) -> Mapping[str, Any] | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return _try_load_json_object(text[start : index + 1])

    return None


def _read_required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise MeetingIntelligenceContractError(f"'{key}' must be a string")
    return value


def _read_speaker_mapping(payload: Mapping[str, Any]) -> dict[str, str]:
    value = payload.get("speaker_mapping")
    if not isinstance(value, dict):
        raise MeetingIntelligenceContractError("'speaker_mapping' must be an object")

    mapping: dict[str, str] = {}
    for raw_label, raw_name in value.items():
        if not isinstance(raw_label, str) or not isinstance(raw_name, str):
            raise MeetingIntelligenceContractError(
                "speaker_mapping keys and values must be strings"
            )
        label = raw_label.strip()
        name = raw_name.strip()
        if not label or not name:
            raise MeetingIntelligenceContractError(
                "speaker_mapping entries must not contain empty labels or names"
            )
        mapping[label] = name

    return mapping