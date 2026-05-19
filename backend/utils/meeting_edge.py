from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backend.utils.meeting_notes import (
    MeetingEventContext,
    build_meeting_context_prompt_section,
    build_user_notes_prompt_section,
)


JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

DEFAULT_MEETING_EDGE_PROMPT_TEMPLATE = """You are Meeting Edge, a live meeting assistant.

Your task is to produce concise, high-signal, real-time guidance that helps the user participate more effectively in the current meeting.

Return one valid JSON object with these keys:
- `summary`: 1-2 sentences on what matters right now.
- `questions`: 0-3 smart clarifying or high-leverage questions the user could ask next.
- `points`: 0-3 overlooked points, risks, or considerations the user could raise.
- `concepts`: 0-2 brief explanations of technical or domain terms that were actually mentioned and would help the user follow the discussion.

# Critical Rules
- Be tactful, professional, constructive, and non-manipulative.
- Do not invent facts, commitments, requirements, or attendee intent.
- Prefer fewer items over weak items.
- Align suggestions with the user's stated focus when provided.
- Only explain concepts that were actually mentioned or clearly implied by the recent transcript.
- Keep each question, point, and explanation concise.
- Return valid JSON only. Do not include prose before or after the JSON object.

# Required JSON Schema
{{
    "summary": "One or two sentence read of the meeting.",
    "questions": ["Question the user could ask"],
    "points": ["Point the user could raise"],
    "concepts": [
        {{
            "term": "Technical term",
            "explanation": "Short explanation"
        }}
    ]
}}

# Earlier Context Summary
{rolling_summary_section}

# User Focus
{focus_text_section}

# User Notes Context
{user_notes_section}

# Meeting Context
{meeting_context_section}

# Recent Transcript
{recent_transcript}
"""


class MeetingEdgeContractError(ValueError):
    """Raised when a Meeting Edge payload breaks the JSON contract."""


@dataclass(frozen=True)
class MeetingEdgeRequest:
    recent_transcript: str
    rolling_summary: str | None = None
    focus_text: str | None = None
    user_notes: str | None = None
    meeting_context: MeetingEventContext | None = None

    def __post_init__(self) -> None:
        transcript = self.recent_transcript.strip()
        if not transcript:
            raise MeetingEdgeContractError(
                "recent_transcript must be a non-empty string"
            )

        object.__setattr__(self, "recent_transcript", transcript)
        object.__setattr__(
            self,
            "rolling_summary",
            _normalize_optional_text(self.rolling_summary),
        )
        object.__setattr__(self, "focus_text", _normalize_optional_text(self.focus_text))
        object.__setattr__(self, "user_notes", _normalize_optional_text(self.user_notes))


@dataclass(frozen=True)
class MeetingEdgeConcept:
    term: str
    explanation: str

    def __post_init__(self) -> None:
        term = re.sub(r"\s+", " ", self.term.strip())
        explanation = re.sub(r"\s+", " ", self.explanation.strip())
        if not term:
            raise MeetingEdgeContractError("concept.term must be a non-empty string")
        if not explanation:
            raise MeetingEdgeContractError(
                "concept.explanation must be a non-empty string"
            )

        object.__setattr__(self, "term", term)
        object.__setattr__(self, "explanation", explanation)


@dataclass(frozen=True)
class MeetingEdgeResult:
    summary: str
    questions: tuple[str, ...]
    points: tuple[str, ...]
    concepts: tuple[MeetingEdgeConcept, ...]

    def __post_init__(self) -> None:
        summary = re.sub(r"\s+", " ", self.summary.strip())
        if not summary:
            raise MeetingEdgeContractError("summary must be a non-empty string")

        questions = _normalize_string_items(self.questions, max_items=3)
        points = _normalize_string_items(self.points, max_items=3)
        concepts = tuple(self.concepts[:2])

        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "questions", questions)
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "concepts", concepts)


def get_default_meeting_edge_prompt_template() -> str:
    return DEFAULT_MEETING_EDGE_PROMPT_TEMPLATE


def build_meeting_edge_prompt(
    request: MeetingEdgeRequest,
    prompt_template: str | None = None,
) -> str:
    template = prompt_template or get_default_meeting_edge_prompt_template()
    return template.format(
        recent_transcript=request.recent_transcript,
        rolling_summary_section=build_rolling_summary_prompt_section(
            request.rolling_summary
        ),
        focus_text_section=build_focus_text_prompt_section(request.focus_text),
        user_notes_section=build_user_notes_prompt_section(request.user_notes),
        meeting_context_section=build_meeting_context_prompt_section(
            request.meeting_context
        ),
    )


def parse_meeting_edge_response(
    response_text: str,
    *,
    request: MeetingEdgeRequest | None = None,
) -> MeetingEdgeResult:
    payload = _load_meeting_edge_payload(response_text)
    result = MeetingEdgeResult(
        summary=_read_required_string(payload, "summary"),
        questions=_read_string_list(payload, "questions", max_items=3),
        points=_read_string_list(payload, "points", max_items=3),
        concepts=_read_concepts(payload),
    )

    if request is not None and not result.questions and not result.points and not result.concepts:
        raise MeetingEdgeContractError(
            "Meeting Edge response must include at least one question, point, or concept"
        )

    return result


def serialize_meeting_edge_result(result: MeetingEdgeResult) -> dict[str, Any]:
    return {
        "summary": result.summary,
        "questions": list(result.questions),
        "points": list(result.points),
        "concepts": [
            {"term": concept.term, "explanation": concept.explanation}
            for concept in result.concepts
        ],
    }


def build_rolling_summary_prompt_section(rolling_summary: str | None) -> str:
    if not rolling_summary:
        return "No earlier summary is available yet. Infer context from the recent transcript only."
    return rolling_summary


def build_focus_text_prompt_section(focus_text: str | None) -> str:
    if not focus_text:
        return "No explicit user focus was provided. Infer the most constructive general guidance from the meeting context."
    return focus_text


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = re.sub(r"\s+", " ", str(value).strip())
    return cleaned or None


def _normalize_string_items(
    items: Sequence[str],
    *,
    max_items: int,
) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in items[:max_items]:
        cleaned = re.sub(r"\s+", " ", str(item).strip())
        if cleaned:
            normalized.append(cleaned)
    return tuple(normalized)


def _load_meeting_edge_payload(response_text: str) -> Mapping[str, Any]:
    text = response_text.strip()
    if not text:
        raise MeetingEdgeContractError("response_text must be a non-empty string")

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

    raise MeetingEdgeContractError(
        "Could not parse a Meeting Edge JSON object from the response"
    )


def _try_load_json_object(candidate: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        raise MeetingEdgeContractError("Meeting Edge response must be a JSON object")

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
    if not isinstance(value, str) or not value.strip():
        raise MeetingEdgeContractError(f"{key} must be a non-empty string")
    return value


def _read_string_list(
    payload: Mapping[str, Any],
    key: str,
    *,
    max_items: int,
) -> tuple[str, ...]:
    value = payload.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MeetingEdgeContractError(f"{key} must be an array")
    return _normalize_string_items(tuple(str(item) for item in value), max_items=max_items)


def _read_concepts(payload: Mapping[str, Any]) -> tuple[MeetingEdgeConcept, ...]:
    value = payload.get("concepts", [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MeetingEdgeContractError("concepts must be an array")

    concepts: list[MeetingEdgeConcept] = []
    for item in value[:2]:
        if not isinstance(item, Mapping):
            raise MeetingEdgeContractError("Each concept must be an object")
        concepts.append(
            MeetingEdgeConcept(
                term=_read_required_string(item, "term"),
                explanation=_read_required_string(item, "explanation"),
            )
        )

    return tuple(concepts)