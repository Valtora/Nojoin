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

MEETING_EDGE_CONTEXT_LEVEL_MIN = 1
MEETING_EDGE_CONTEXT_LEVEL_MAX = 5
MEETING_EDGE_CONTEXT_LEVEL_DEFAULT = 2

MEETING_EDGE_CONTEXT_GUIDANCE = {
    1: "Extremely selective (Most Complex). ONLY explain highly specialized, rare technical terms, advanced domain jargon, or obscure acronyms that an expert would use. Absolutely do not explain common business, general software, product, or workplace terms (such as API, database, backend, frontend, server, sprint, deployment, roadmap, blocker, metrics, or checklist). If in doubt, do not explain the term.",
    2: "Highly selective. Explain only advanced, domain-specific, or proprietary jargon that cross-functional attendees might not understand. Skip standard industry terms and common workplace language.",
    3: "Selective. Explain technical or domain-specific terms that a general professional might not know. Skip common professional terminology and widely known software concepts.",
    4: "Balanced. Explain moderately technical or abbreviated terms when a short clarification would help a non-specialist follow along. Skip plain-language terms.",
    5: "Detailed (Least Complex). Be generous with clarifications for non-trivial technical, domain-specific, or abbreviated terms, while still avoiding obvious plain-language words.",
}

DEFAULT_MEETING_EDGE_PROMPT_TEMPLATE = """You are Meeting Edge, a live meeting assistant.

Your task is to produce concise, high-signal, real-time guidance that helps the user participate more effectively in the current meeting.

Return one valid JSON object with these keys:
- `summary`: 1-2 sentences on what matters right now.
- `questions`: 0-3 smart clarifying or high-leverage questions the user could ask next.
- `points`: 0-3 overlooked points, risks, or considerations the user could raise.
- `concepts`: brief explanations of the technical or domain terms that were actually mentioned and would help the user follow the discussion, but only when they meet the Technical Context Policy below.

# Critical Rules
- Be tactful, professional, constructive, and non-manipulative.
- Do not invent facts, commitments, requirements, or attendee intent.
- Prefer fewer items over weak items.
- Align suggestions with the user's stated focus when provided.
- Only explain concepts that were actually mentioned or clearly implied by the recent transcript.
- Include all distinct concepts from the recent transcript that materially help comprehension, not just the top one or two.
- Keep each question, point, and explanation concise.
- Return valid JSON only. Do not include prose before or after the JSON object.

# Technical Context Policy
{concept_guidance_section}

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
    context_level: int = MEETING_EDGE_CONTEXT_LEVEL_DEFAULT

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
        object.__setattr__(self, "context_level", _normalize_context_level(self.context_level))


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
        concepts = tuple(self.concepts)

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
        concept_guidance_section=build_concept_guidance_prompt_section(request.context_level),
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


def _are_singular_plural(t1: str, t2: str) -> bool:
    # Lowercase and strip whitespace comparisons
    if t1 == t2:
        return True

    # Identify singular vs plural candidate by length
    if len(t1) < len(t2):
        singular, plural = t1, t2
    else:
        singular, plural = t2, t1

    # Simple pluralization rules
    if singular + "s" == plural:
        return True
    if singular + "es" == plural:
        return True
    if singular.endswith("y") and singular[:-1] + "ies" == plural:
        return True
    return False


def deduplicate_concepts(concepts: Sequence[MeetingEdgeConcept]) -> list[MeetingEdgeConcept]:
    deduped: list[MeetingEdgeConcept] = []
    for concept in concepts:
        found_idx = -1
        for i, existing in enumerate(deduped):
            if _are_singular_plural(existing.term.casefold(), concept.term.casefold()):
                found_idx = i
                break
        if found_idx >= 0:
            existing = deduped[found_idx]
            # Keep the shorter (singular) term, but ALWAYS use the newer explanation
            term = concept.term if len(concept.term) < len(existing.term) else existing.term
            deduped[found_idx] = MeetingEdgeConcept(term=term, explanation=concept.explanation)
        else:
            deduped.append(concept)
    return deduped


def merge_meeting_edge_concept_history(
    previous_payload: Mapping[str, Any] | None,
    current_payload: Mapping[str, Any] | None,
    *,
    reset_history: bool = False,
) -> list[dict[str, str]]:
    if reset_history:
        current_concepts = _read_serialized_concepts(current_payload, "concepts")
        return _serialize_concepts(deduplicate_concepts(current_concepts))

    previous_concepts = _read_concept_history_items(previous_payload)
    current_concepts = _read_serialized_concepts(current_payload, "concepts")

    merged = list(previous_concepts) + list(current_concepts)
    deduped = deduplicate_concepts(merged)

    return _serialize_concepts(deduped)


def build_rolling_summary_prompt_section(rolling_summary: str | None) -> str:
    if not rolling_summary:
        return "No earlier summary is available yet. Infer context from the recent transcript only."
    return rolling_summary


def build_focus_text_prompt_section(focus_text: str | None) -> str:
    if not focus_text:
        return "No explicit user focus was provided. Infer the most constructive general guidance from the meeting context."
    return focus_text


def build_concept_guidance_prompt_section(context_level: int) -> str:
    level = _normalize_context_level(context_level)
    return MEETING_EDGE_CONTEXT_GUIDANCE[level]


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = re.sub(r"\s+", " ", str(value).strip())
    return cleaned or None


def _normalize_context_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return MEETING_EDGE_CONTEXT_LEVEL_DEFAULT

    return max(MEETING_EDGE_CONTEXT_LEVEL_MIN, min(MEETING_EDGE_CONTEXT_LEVEL_MAX, level))


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
    for item in value:
        if not isinstance(item, Mapping):
            raise MeetingEdgeContractError("Each concept must be an object")
        concepts.append(
            MeetingEdgeConcept(
                term=_read_required_string(item, "term"),
                explanation=_read_required_string(item, "explanation"),
            )
        )

    return tuple(concepts)


def _serialize_concepts(concepts: Sequence[MeetingEdgeConcept]) -> list[dict[str, str]]:
    return [
        {
            "term": concept.term,
            "explanation": concept.explanation,
        }
        for concept in concepts
    ]


def _read_concept_history_items(
    payload: Mapping[str, Any] | None,
) -> tuple[MeetingEdgeConcept, ...]:
    history = _read_serialized_concepts(payload, "concept_history")
    if history:
        return history
    return _read_serialized_concepts(payload, "concepts")


def _read_serialized_concepts(
    payload: Mapping[str, Any] | None,
    key: str,
) -> tuple[MeetingEdgeConcept, ...]:
    if not isinstance(payload, Mapping):
        return ()

    value = payload.get(key, [])
    if value is None or not isinstance(value, list):
        return ()

    concepts: list[MeetingEdgeConcept] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            concepts.append(
                MeetingEdgeConcept(
                    term=_read_required_string(item, "term"),
                    explanation=_read_required_string(item, "explanation"),
                )
            )
        except MeetingEdgeContractError:
            continue

    return tuple(concepts)