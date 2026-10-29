from __future__ import annotations

import ast
import json
import logging
import re
import warnings
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backend.prompts import render_prompt
from backend.utils.meeting_notes import (
    MeetingEventContext,
    build_glossary_prompt_section,
    build_meeting_context_prompt_section,
    build_user_notes_prompt_section,
)
from backend.utils.prompt_blocks import render_prompt_blocks

logger = logging.getLogger(__name__)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

MEETING_EDGE_MAX_ITEMS = 3

# A model sometimes wraps a question or point in an object. These are the keys
# whose string value is taken as the item's text, in this order; an object with
# none of them is dropped.
MEETING_EDGE_ITEM_TEXT_KEYS = ("text", "question", "point", "content")

# Items stored before issue #652 hold the Python repr of such an object, for
# example "{'text': 'Ask Alice Example ...'}". Only a string this short is read
# back as one, and only with ast.literal_eval.
MEETING_EDGE_ITEM_REPR_MAX_CHARS = 2000

MEETING_EDGE_CONTEXT_LEVEL_MIN = 1
MEETING_EDGE_CONTEXT_LEVEL_MAX = 5
MEETING_EDGE_CONTEXT_LEVEL_DEFAULT = 2

MEETING_EDGE_CONTEXT_GUIDANCE = {
    1: render_prompt("meeting_edge.context_level_1"),
    2: render_prompt("meeting_edge.context_level_2"),
    3: render_prompt("meeting_edge.context_level_3"),
    4: render_prompt("meeting_edge.context_level_4"),
    5: render_prompt("meeting_edge.context_level_5"),
}

# Heading that starts the volatile per-refresh context. The halves are composed
# separately so provider caching never depends on searching the rendered prose.
MEETING_EDGE_CACHE_SPLIT_MARKER = render_prompt(
    "shared.heading_earlier_context_summary"
)

# Static prompt text, split at the cache boundary. Plain strings, never format
# templates: the JSON schema uses real braces and interpolated values need no
# escaping. See backend.utils.prompt_blocks.
MEETING_EDGE_INTRO = render_prompt("meeting_edge.intro")

MEETING_EDGE_CRITICAL_RULES = render_prompt("meeting_edge.critical_rules")

MEETING_EDGE_JSON_SCHEMA = render_prompt("meeting_edge.json_schema")


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
    # Merged install + personal glossary. Sits in the cache-stable prefix, so
    # editing it invalidates the cached prefix once and then re-warms.
    glossary: str | None = None
    previous_questions: tuple[str, ...] = ()
    previous_points: tuple[str, ...] = ()

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
        object.__setattr__(
            self, "focus_text", _normalize_optional_text(self.focus_text)
        )
        object.__setattr__(
            self, "user_notes", _normalize_optional_text(self.user_notes)
        )
        object.__setattr__(
            self, "context_level", _normalize_context_level(self.context_level)
        )
        object.__setattr__(
            self,
            "previous_questions",
            normalize_meeting_edge_items(self.previous_questions or ()),
        )
        object.__setattr__(
            self,
            "previous_points",
            normalize_meeting_edge_items(self.previous_points or ()),
        )


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
    rolling_summary: str | None = None

    def __post_init__(self) -> None:
        summary = re.sub(r"\s+", " ", self.summary.strip())
        if not summary:
            raise MeetingEdgeContractError("summary must be a non-empty string")

        questions = normalize_meeting_edge_items(self.questions)
        points = normalize_meeting_edge_items(self.points)
        concepts = tuple(self.concepts)

        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "questions", questions)
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "concepts", concepts)
        object.__setattr__(
            self,
            "rolling_summary",
            _normalize_optional_text(self.rolling_summary),
        )


def build_meeting_edge_prompt_parts(
    request: MeetingEdgeRequest,
    prompt_override: str | None = None,
) -> tuple[str, str]:
    """Compose the Meeting Edge prompt as a cache-stable prefix and a volatile suffix.

    The prefix (system instructions, technical-context policy, glossary, and JSON
    schema) is identical across refreshes of the same meeting, so a provider that
    supports prompt caching can cache it. The suffix carries the per-refresh
    context (rolling summary, previous suggestions, notes, recent transcript).
    The two always concatenate to the whole prompt, so the model sees the same
    text either way; only the cache breakpoint differs.

    Composing the halves separately is what makes that guarantee structural,
    rather than finding MEETING_EDGE_CACHE_SPLIT_MARKER inside a rendered
    string, which a heading rename would break.

    Returns ``("", prompt)`` for a caller-supplied override, which callers treat
    as "do not cache".
    """
    if prompt_override:
        return "", prompt_override

    prefix = render_prompt_blocks(
        [
            (None, MEETING_EDGE_INTRO),
            (
                render_prompt("shared.heading_critical_rules"),
                MEETING_EDGE_CRITICAL_RULES,
            ),
            (
                render_prompt("shared.heading_technical_context_policy"),
                build_concept_guidance_prompt_section(request.context_level),
            ),
            (
                render_prompt("shared.heading_glossary"),
                build_glossary_prompt_section(request.glossary, for_notes=False),
            ),
            (
                render_prompt("shared.heading_required_json_schema"),
                MEETING_EDGE_JSON_SCHEMA,
            ),
        ]
    )
    suffix = render_prompt_blocks(
        [
            (
                MEETING_EDGE_CACHE_SPLIT_MARKER,
                build_rolling_summary_prompt_section(request.rolling_summary),
            ),
            (
                render_prompt("shared.heading_previously_suggested"),
                build_previous_suggestions_prompt_section(
                    request.previous_questions,
                    request.previous_points,
                ),
            ),
            (
                render_prompt("shared.heading_user_focus"),
                build_focus_text_prompt_section(request.focus_text),
            ),
            (
                render_prompt("shared.heading_user_notes_context"),
                build_user_notes_prompt_section(request.user_notes),
            ),
            (
                render_prompt("shared.heading_meeting_context"),
                build_meeting_context_prompt_section(request.meeting_context),
            ),
            (
                render_prompt("shared.heading_recent_transcript"),
                request.recent_transcript,
            ),
        ]
    )
    return f"{prefix}\n\n", f"{suffix}\n"


def build_meeting_edge_prompt(
    request: MeetingEdgeRequest,
    prompt_override: str | None = None,
) -> str:
    prefix, suffix = build_meeting_edge_prompt_parts(request, prompt_override)
    return f"{prefix}{suffix}"


def parse_meeting_edge_response(
    response_text: str,
    *,
    request: MeetingEdgeRequest | None = None,
) -> MeetingEdgeResult:
    payload = _load_meeting_edge_payload(response_text)
    result = MeetingEdgeResult(
        summary=_read_required_string(payload, "summary"),
        questions=_read_string_list(payload, "questions"),
        points=_read_string_list(payload, "points"),
        concepts=_read_concepts(payload),
        rolling_summary=_read_optional_string(payload, "rolling_summary"),
    )

    return result


def serialize_meeting_edge_result(result: MeetingEdgeResult) -> dict[str, Any]:
    return {
        "summary": result.summary,
        "rolling_summary": result.rolling_summary,
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


def _normalize_concept_term(term: str) -> str:
    # Casefold and collapse hyphen/whitespace variants ("real-time" == "real time").
    cleaned = re.sub(r"[\s\-_]+", " ", term.casefold()).strip()
    return re.sub(r"[.]", "", cleaned)


def _acronym_of(term: str) -> str | None:
    words = [word for word in re.split(r"[\s\-_/]+", term.strip()) if word]
    if len(words) < 2:
        return None
    return "".join(word[0] for word in words).casefold()


def _are_acronym_and_expansion(t1: str, t2: str) -> bool:
    # "LLM" vs "Large Language Model" (compact term must look like an acronym).
    if len(t1) > len(t2):
        t1, t2 = t2, t1
    compact = re.sub(r"[.\s]", "", t1)
    if not (2 <= len(compact) <= 8) or not compact.isalpha():
        return False
    expansion_acronym = _acronym_of(t2)
    return expansion_acronym is not None and compact.casefold() == expansion_acronym


def _are_equivalent_concept_terms(t1: str, t2: str) -> bool:
    n1 = _normalize_concept_term(t1)
    n2 = _normalize_concept_term(t2)
    if _are_singular_plural(n1, n2):
        return True
    return _are_acronym_and_expansion(t1, t2)


def deduplicate_concepts(
    concepts: Sequence[MeetingEdgeConcept],
) -> list[MeetingEdgeConcept]:
    deduped: list[MeetingEdgeConcept] = []
    for concept in concepts:
        found_idx = -1
        for i, existing in enumerate(deduped):
            if _are_equivalent_concept_terms(existing.term, concept.term):
                found_idx = i
                break
        if found_idx >= 0:
            existing = deduped[found_idx]
            # Keep the shorter (singular) term, but ALWAYS use the newer explanation
            term = (
                concept.term
                if len(concept.term) < len(existing.term)
                else existing.term
            )
            deduped[found_idx] = MeetingEdgeConcept(
                term=term, explanation=concept.explanation
            )
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
        return render_prompt("meeting_edge.no_rolling_summary")
    return rolling_summary


def build_previous_suggestions_prompt_section(
    previous_questions: Sequence[str],
    previous_points: Sequence[str],
) -> str:
    questions = [
        str(item).strip() for item in previous_questions or () if str(item).strip()
    ]
    points = [str(item).strip() for item in previous_points or () if str(item).strip()]

    if not questions and not points:
        return render_prompt("meeting_edge.no_previous_suggestions")

    lines: list[str] = []
    if questions:
        lines.append(render_prompt("meeting_edge.previous_questions_label"))
        lines.extend(f"- {question}" for question in questions)
    if points:
        if lines:
            lines.append("")
        lines.append(render_prompt("meeting_edge.previous_points_label"))
        lines.extend(f"- {point}" for point in points)
    return "\n".join(lines)


def build_focus_text_prompt_section(focus_text: str | None) -> str:
    if not focus_text:
        return render_prompt("meeting_edge.no_focus")
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

    return max(
        MEETING_EDGE_CONTEXT_LEVEL_MIN, min(MEETING_EDGE_CONTEXT_LEVEL_MAX, level)
    )


def meeting_edge_item_text(item: Any) -> str | None:
    """The text of one question or point, or None when it carries none.

    A string is its own text unless it is the stored repr of a wrapped item.
    An object gives the first non-empty string under
    MEETING_EDGE_ITEM_TEXT_KEYS. Anything else (a number, a list, a concept
    filed in the wrong list) has no text.
    """
    if isinstance(item, str):
        wrapped = _read_item_repr(item)
        if wrapped is None:
            return _clean_item_text(item)
        item = wrapped
    if not isinstance(item, Mapping):
        return None
    for key in MEETING_EDGE_ITEM_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and (text := _clean_item_text(value)):
            return text
    return None


def normalize_meeting_edge_items(
    items: Sequence[Any],
    *,
    max_items: int = MEETING_EDGE_MAX_ITEMS,
) -> tuple[str, ...]:
    texts, _dropped = _collect_item_texts(items, max_items=max_items)
    return texts


def _collect_item_texts(
    items: Sequence[Any],
    *,
    max_items: int,
) -> tuple[tuple[str, ...], int]:
    """Up to max_items texts, and how many items carried no text.

    Items without text are skipped before the cap, so one never takes the
    place of a later item. Blank strings are skipped without being counted.
    """
    texts: list[str] = []
    dropped = 0
    for item in items:
        text = meeting_edge_item_text(item)
        if text is None:
            if not isinstance(item, str) or item.strip():
                dropped += 1
        elif len(texts) < max_items:
            texts.append(text)
    return tuple(texts), dropped


def _clean_item_text(value: str) -> str | None:
    return re.sub(r"\s+", " ", value.strip()) or None


def _read_item_repr(value: str) -> Mapping[str, Any] | None:
    candidate = value.strip()
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return None
    if len(candidate) > MEETING_EDGE_ITEM_REPR_MAX_CHARS:
        return None
    try:
        with warnings.catch_warnings():
            # A stray backslash in the text is a SyntaxWarning, not an error.
            warnings.simplefilter("ignore", SyntaxWarning)
            parsed = ast.literal_eval(candidate)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


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


def _read_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _read_string_list(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MeetingEdgeContractError(f"{key} must be an array")
    texts, dropped = _collect_item_texts(value, max_items=MEETING_EDGE_MAX_ITEMS)
    if dropped:
        # A count only: the items are meeting content.
        logger.warning("Meeting Edge dropped %d %s item(s) with no text", dropped, key)
    return texts


def _read_concepts(payload: Mapping[str, Any]) -> tuple[MeetingEdgeConcept, ...]:
    value = payload.get("concepts", [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MeetingEdgeContractError("concepts must be an array")

    concepts = _concepts_from_items(value)
    dropped = len(value) - len(concepts)
    if dropped:
        logger.warning("Meeting Edge dropped %d malformed concept(s)", dropped)
    return concepts


def _concepts_from_items(items: Sequence[Any]) -> tuple[MeetingEdgeConcept, ...]:
    """The well-formed concepts in items; a malformed one is skipped."""
    concepts: list[MeetingEdgeConcept] = []
    for item in items:
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

    return _concepts_from_items(value)
