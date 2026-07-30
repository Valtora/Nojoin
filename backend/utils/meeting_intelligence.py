from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from backend.utils.languages import build_output_language_prompt_section
from backend.utils.meeting_notes import (
    AttachedDocument,
    MeetingEventContext,
    MeetingMetadata,
    append_user_notes_section,
    build_documents_prompt_section,
    build_glossary_prompt_section,
    build_meeting_context_prompt_section,
    build_meeting_metadata_prompt_section,
    build_notes_body_spec,
    build_user_notes_prompt_section,
    is_placeholder_speaker_name,
    resolve_recording_speaker_name,
    strip_leading_title_heading,
)
from backend.utils.prompt_blocks import render_prompt_blocks

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

# Static prompt text. These are plain strings, never format templates: the JSON
# schema below can therefore be written with real braces instead of doubled ones,
# and nothing interpolated into the prompt needs escaping. See
# backend.utils.prompt_blocks for why.
AUTOMATIC_MEETING_INTELLIGENCE_INTRO = """You are an expert meeting-notes assistant.

Your task is to produce one valid JSON object that combines:
1. speaker suggestions for unresolved diarization labels only
2. a meeting title
3. final meeting notes in Markdown"""

AUTOMATIC_MEETING_INTELLIGENCE_CRITICAL_RULES = """- Treat any non-generic speaker names already present in the transcript as trusted.
- Only use `speaker_mapping` for the unresolved labels listed below.
- If you are not confident about a label, omit it from `speaker_mapping` and keep the generic label in `notes_markdown`.
- The names or roles used in `notes_markdown` for unresolved labels must match the returned `speaker_mapping` entries exactly.
- The `title` and `notes_markdown` must reflect the same meeting interpretation.
- Return valid JSON only. Do not include prose before or after the JSON object.
- Escape newlines inside `notes_markdown` according to JSON rules."""

# The ``\\n`` sequences are literal backslash-n in the rendered prompt: the schema
# is showing the model how to escape newlines inside a JSON string.
AUTOMATIC_MEETING_INTELLIGENCE_JSON_SCHEMA = """{
    "speaker_mapping": {
        "SPEAKER_00": "Person name or role"
    },
    "title": "Meeting title",
    "notes_markdown": "## Localized summary heading\\n\\n...\\n\\n## Localized section heading\\n..."
}"""


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
    output_language_instruction: str | None = None
    # The user's notes structure, or None for the shipped one. Also decides
    # whether the strict opening-heading contract applies to the response.
    notes_sections: str | None = None
    glossary: str | None = None
    meeting_metadata: MeetingMetadata | None = None
    # Parsed text of the meeting's attached documents, rendered inside
    # untrusted-content delimiters. See build_documents_prompt_section.
    documents: Sequence["AttachedDocument"] | None = None

    @property
    def uses_custom_notes_sections(self) -> bool:
        return bool(self.notes_sections)

    def __post_init__(self) -> None:
        transcript = self.resolved_transcript.strip()
        if not transcript:
            raise MeetingIntelligenceContractError(
                "resolved_transcript must be a non-empty string"
            )

        normalized_labels = tuple(
            str(label).strip()
            for label in self.unresolved_speakers
            if str(label).strip()
        )
        if len(set(normalized_labels)) != len(normalized_labels):
            raise MeetingIntelligenceContractError(
                "unresolved_speakers must not contain duplicates"
            )

        normalized_user_notes = self.user_notes.strip() if self.user_notes else None
        normalized_output_language_instruction = (
            self.output_language_instruction.strip()
            if self.output_language_instruction
            else None
        )

        object.__setattr__(self, "resolved_transcript", transcript)
        object.__setattr__(self, "unresolved_speakers", normalized_labels)
        object.__setattr__(self, "user_notes", normalized_user_notes or None)
        object.__setattr__(
            self,
            "output_language_instruction",
            normalized_output_language_instruction or None,
        )
        object.__setattr__(
            self,
            "notes_sections",
            (self.notes_sections.strip() or None) if self.notes_sections else None,
        )
        object.__setattr__(
            self,
            "glossary",
            (self.glossary.strip() or None) if self.glossary else None,
        )

    @property
    def has_unresolved_speakers(self) -> bool:
        return bool(self.unresolved_speakers)


@dataclass(frozen=True)
class AutomaticMeetingIntelligenceResult:
    """Normalized result for the automatic unified meeting-intelligence call."""

    speaker_mapping: dict[str, str]
    title: str
    notes_markdown: str
    # The opening-``##``-heading rule below is the built-in structure's own first
    # line, not a property of well-formed notes, so it cannot be enforced against
    # a structure the user wrote. Runs on a custom template set this False; every
    # other check still applies.
    require_section_heading: bool = True

    def __post_init__(self) -> None:
        normalized_mapping = {
            str(label).strip(): str(name).strip()
            for label, name in self.speaker_mapping.items()
            if str(label).strip() and str(name).strip()
        }
        title = re.sub(r"\s+", " ", self.title.strip())
        # Notes must begin at the Summary section; the application shows the meeting
        # title separately. Strip a redundant title heading if the model emits one.
        notes_markdown = strip_leading_title_heading(
            self.notes_markdown.replace("\r\n", "\n").strip()
        )

        if not title:
            raise MeetingIntelligenceContractError("title must be a non-empty string")

        if not notes_markdown:
            raise MeetingIntelligenceContractError(
                "notes_markdown must be a non-empty string"
            )

        if self.require_section_heading and not re.match(r"^##\s+\S", notes_markdown):
            raise MeetingIntelligenceContractError(
                "notes_markdown must start with a section heading (## ...)"
            )

        object.__setattr__(self, "speaker_mapping", normalized_mapping)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "notes_markdown", notes_markdown)

    def validate_for_request(
        self,
        request: AutomaticMeetingIntelligenceRequest,
    ) -> None:
        unknown_labels = sorted(
            label
            for label in self.speaker_mapping
            if label not in request.unresolved_speakers
        )
        if unknown_labels:
            raise MeetingIntelligenceContractError(
                "speaker_mapping contains labels that were not unresolved in the request: "
                + ", ".join(unknown_labels)
            )


def get_default_notes_markdown_requirements() -> str:
    """The shipped notes body spec, for tests and the settings preview."""
    return build_notes_body_spec()


def build_automatic_meeting_intelligence_request(
    resolved_transcript: str,
    speakers: Iterable[Any],
    *,
    user_notes: str | None = None,
    prefer_short_titles: bool = True,
    meeting_context: MeetingEventContext | None = None,
    output_language_instruction: str | None = None,
    notes_sections: str | None = None,
    glossary: str | None = None,
    meeting_metadata: MeetingMetadata | None = None,
) -> AutomaticMeetingIntelligenceRequest:
    return AutomaticMeetingIntelligenceRequest(
        resolved_transcript=resolved_transcript,
        unresolved_speakers=get_speakers_eligible_for_llm_renaming(speakers),
        user_notes=user_notes,
        prefer_short_titles=prefer_short_titles,
        meeting_context=meeting_context,
        output_language_instruction=output_language_instruction,
        notes_sections=notes_sections,
        glossary=glossary,
        meeting_metadata=meeting_metadata,
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
    prompt_override: str | None = None,
) -> str:
    """Compose the unified prompt.

    ``prompt_override`` replaces the whole prompt verbatim; it is a test seam, not
    a template, and nothing in the application passes it.
    """
    if prompt_override:
        return prompt_override

    body = render_prompt_blocks(
        [
            (None, AUTOMATIC_MEETING_INTELLIGENCE_INTRO),
            ("# Critical Rules", AUTOMATIC_MEETING_INTELLIGENCE_CRITICAL_RULES),
            (
                "# Title Style",
                build_title_preference_instruction(request.prefer_short_titles),
            ),
            (
                "# Unresolved Speaker Labels",
                build_unresolved_speakers_prompt_section(request.unresolved_speakers),
            ),
            (
                "# Recording Metadata",
                build_meeting_metadata_prompt_section(request.meeting_metadata),
            ),
            ("# Glossary", build_glossary_prompt_section(request.glossary)),
            (
                "# User Notes Context",
                build_user_notes_prompt_section(request.user_notes),
            ),
            (
                "# Meeting Context",
                build_meeting_context_prompt_section(request.meeting_context),
            ),
            (
                "# Attached Documents",
                build_documents_prompt_section(request.documents),
            ),
            # Carries its own heading.
            (
                None,
                build_output_language_prompt_section(
                    request.output_language_instruction
                ),
            ),
            ("# Required JSON Schema", AUTOMATIC_MEETING_INTELLIGENCE_JSON_SCHEMA),
            (
                "# Notes Markdown Requirements",
                build_notes_body_spec(request.notes_sections),
            ),
            ("# Transcript", request.resolved_transcript),
        ]
    )
    return f"{body}\n"


def apply_speaker_mapping_to_notes(
    notes_markdown: str, speaker_mapping: Mapping[str, str]
) -> str:
    """Replace any residual diarization labels left in the notes with their
    mapped names.

    The prompt asks the model to write the inferred name inline in
    ``notes_markdown`` for every label it also returns in ``speaker_mapping``.
    Weaker models (notably the local Ollama fallback) sometimes return a correct
    mapping but still leave the raw ``SPEAKER_XX`` label in the prose. This makes
    the notes robust to that: for every mapped label we substitute the raw label
    token with its name. Labels absent from the mapping (the model was not
    confident) are intentionally left untouched, matching the prompt contract.
    """
    if not notes_markdown or not speaker_mapping:
        return notes_markdown

    # Single left-to-right pass over a whole-word alternation so a replacement
    # is never re-scanned (avoids cascading when a name contains a label-like
    # token). Longest labels first so e.g. SPEAKER_10 wins over SPEAKER_1.
    labels = sorted(speaker_mapping, key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(label) for label in labels) + r")\b"
    )
    return pattern.sub(lambda match: speaker_mapping[match.group(1)], notes_markdown)


def finalise_automatic_meeting_intelligence_result(
    result: AutomaticMeetingIntelligenceResult,
    user_notes: str | None,
) -> AutomaticMeetingIntelligenceResult:
    notes_markdown = apply_speaker_mapping_to_notes(
        result.notes_markdown, result.speaker_mapping
    )
    return AutomaticMeetingIntelligenceResult(
        speaker_mapping=result.speaker_mapping,
        title=result.title,
        notes_markdown=append_user_notes_section(notes_markdown, user_notes),
        require_section_heading=result.require_section_heading,
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
        require_section_heading=not (
            request is not None and request.uses_custom_notes_sections
        ),
    )

    if request is not None:
        result.validate_for_request(request)

    return result


def _load_meeting_intelligence_payload(response_text: str) -> Mapping[str, Any]:
    text = response_text.strip()
    if not text:
        raise MeetingIntelligenceContractError(
            "response_text must be a non-empty string"
        )

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
