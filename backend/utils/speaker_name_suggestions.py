from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from backend.utils.meeting_notes import MeetingEventContext, is_placeholder_speaker_name

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
# Only the cue phrase is case-insensitive. The captured name must be capitalised,
# because capitalisation is the only evidence that "I'm Priya" names someone while
# "I'm going to share" does not. A lowercase transcript therefore yields no
# rule-based suggestion rather than a guessed one.
# Engines emit the typographic apostrophe (’) as well as the straight one, so
# every apostrophe below accepts both.
_CAPITALISED_NAME = r"([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,2})"
SELF_INTRO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(rf"\b(?i:i am|i['’]m|my name is|this is)\s+{_CAPITALISED_NAME}\b"),
        "self_introduction",
    ),
    (
        # Every line starts with a capital in a cased transcript, so here the
        # cue must close the clause: "Priya here." but not "But here's".
        re.compile(rf"^\s*{_CAPITALISED_NAME}\s+(?i:here|speaking)\s*(?:[.,!;:]|$)"),
        "self_identification",
    ),
)
DISALLOWED_NAME_TOKENS = {
    "a",
    "an",
    "agenda",
    "best",
    "client",
    "company",
    "customer",
    "engineer",
    "everyone",
    "folks",
    "hello",
    "hi",
    "manager",
    "meeting",
    "morning",
    "right",
    "same",
    "that",
    "the",
    "thing",
    "this",
    "team",
    "thanks",
}
NAME_STOPWORDS = {"at", "for", "from", "in", "on", "with"}
# Words that follow "I'm", "my name is", "this is", or precede "here"/"speaking",
# without being a name. They are capitalised in title-case or all-caps
# transcripts and at sentence starts ("Just here to listen", "Generally
# speaking"). A name ends where one of these begins. Words that are also common
# first names (Will, May, Frank, Grace) are deliberately absent.
NON_NAME_WORDS = frozenset(
    """
    about actually afraid all almost already also always another anyone aware
    back basically because coming confused curious definitely doing done down
    everybody excited exactly fine frankly generally getting glad going gonna
    good great happy he hearing her here his honestly hoping how important
    interested is it its just kind late literally looking my never new nobody
    not now ok okay only our out over personally pretty probably quite ready
    really saying she so somebody someone something sorry sort speaking still
    strictly sure talking technically their there they thinking totally trying
    up very we what when where which who why wondering working worried you your
    """.split()
)
# Contractions and possessives ("I'm here", "this is Tom's laptop") are not
# names; an apostrophe inside a name ("O'Brien") is kept.
CONTRACTION_SUFFIX_PATTERN = re.compile(r"['’](?:m|re|s|ve|ll|d|t)$", re.IGNORECASE)

SPEAKER_SUGGESTION_STATUS_PENDING = "pending"
SPEAKER_SUGGESTION_STATUS_ACCEPTED = "accepted"
SPEAKER_SUGGESTION_STATUS_REJECTED = "rejected"
SPEAKER_SUGGESTION_STATUS_SUPERSEDED = "superseded"


class SpeakerSuggestionContractError(ValueError):
    """Raised when a structured speaker suggestion payload is invalid."""


@dataclass(frozen=True)
class SpeakerSuggestionEvidenceSpan:
    quote: str
    reason: str
    start_seconds: float | None = None
    end_seconds: float | None = None

    def __post_init__(self) -> None:
        quote = str(self.quote).strip()
        reason = str(self.reason).strip()
        if not quote:
            raise SpeakerSuggestionContractError("evidence quote must be non-empty")
        if not reason:
            raise SpeakerSuggestionContractError("evidence reason must be non-empty")

        start_seconds = _normalize_optional_float(self.start_seconds)
        end_seconds = _normalize_optional_float(self.end_seconds)
        if (
            start_seconds is not None
            and end_seconds is not None
            and end_seconds < start_seconds
        ):
            raise SpeakerSuggestionContractError(
                "evidence end_seconds must be greater than or equal to start_seconds"
            )

        object.__setattr__(self, "quote", quote)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "start_seconds", start_seconds)
        object.__setattr__(self, "end_seconds", end_seconds)


@dataclass(frozen=True)
class SpeakerInferenceSuggestion:
    diarization_label: str
    suggested_name: str
    confidence: float
    rationale: str | None = None
    evidence_spans: tuple[SpeakerSuggestionEvidenceSpan, ...] = field(
        default_factory=tuple
    )
    signals: tuple[str, ...] = field(default_factory=tuple)
    source: str = "llm"

    def __post_init__(self) -> None:
        label = str(self.diarization_label).strip()
        suggested_name = str(self.suggested_name).strip()
        confidence = float(self.confidence)
        if not label:
            raise SpeakerSuggestionContractError("diarization_label must be non-empty")
        if not suggested_name:
            raise SpeakerSuggestionContractError("suggested_name must be non-empty")
        if not 0.0 <= confidence <= 1.0:
            raise SpeakerSuggestionContractError(
                "confidence must be between 0.0 and 1.0"
            )

        rationale = str(self.rationale).strip() if self.rationale else None
        source = str(self.source).strip() or "llm"
        evidence_spans = tuple(self.evidence_spans or ())
        signals = tuple(
            dict.fromkeys(
                signal.strip() for signal in self.signals or () if str(signal).strip()
            )
        )

        object.__setattr__(self, "diarization_label", label)
        object.__setattr__(self, "suggested_name", suggested_name)
        object.__setattr__(self, "confidence", round(confidence, 4))
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "evidence_spans", evidence_spans)
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True)
class SpeakerInferenceResult:
    suggestions: tuple[SpeakerInferenceSuggestion, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        normalized: list[SpeakerInferenceSuggestion] = []
        labels: set[str] = set()
        for suggestion in self.suggestions or ():
            if suggestion.diarization_label in labels:
                raise SpeakerSuggestionContractError(
                    f"duplicate suggestion for {suggestion.diarization_label}"
                )
            labels.add(suggestion.diarization_label)
            normalized.append(suggestion)
        object.__setattr__(self, "suggestions", tuple(normalized))

    @property
    def mapping(self) -> dict[str, str]:
        return {
            suggestion.diarization_label: suggestion.suggested_name
            for suggestion in self.suggestions
        }


def parse_speaker_inference_response(
    response_text: str,
    *,
    allowed_labels: Sequence[str] | None = None,
) -> SpeakerInferenceResult:
    payload = _load_json_object(response_text)
    raw_suggestions = payload.get("suggestions")
    if not isinstance(raw_suggestions, list):
        raise SpeakerSuggestionContractError("'suggestions' must be an array")

    allowed = {label.strip() for label in (allowed_labels or ()) if str(label).strip()}
    suggestions: list[SpeakerInferenceSuggestion] = []

    for raw_item in raw_suggestions:
        if not isinstance(raw_item, Mapping):
            raise SpeakerSuggestionContractError(
                "each speaker suggestion must be a JSON object"
            )

        diarization_label = _read_required_string(raw_item, "diarization_label")
        if allowed and diarization_label not in allowed:
            raise SpeakerSuggestionContractError(
                f"unexpected diarization label returned: {diarization_label}"
            )

        evidence_spans = _read_evidence_spans(raw_item)
        if not evidence_spans:
            raise SpeakerSuggestionContractError(
                f"speaker suggestion for {diarization_label} must include evidence_spans"
            )

        suggestions.append(
            SpeakerInferenceSuggestion(
                diarization_label=diarization_label,
                suggested_name=_read_required_string(raw_item, "suggested_name"),
                confidence=_read_required_float(raw_item, "confidence"),
                rationale=_read_optional_string(raw_item, "rationale"),
                evidence_spans=evidence_spans,
                signals=_read_string_list(raw_item.get("signals")),
                source="llm",
            )
        )

    return SpeakerInferenceResult(tuple(suggestions))


def detect_rule_based_speaker_suggestions(
    segments: Sequence[dict[str, Any]],
    eligible_labels: Sequence[str],
    meeting_context: MeetingEventContext | None = None,
) -> SpeakerInferenceResult:
    allowed = {label.strip() for label in eligible_labels if str(label).strip()}
    best_by_label: dict[str, SpeakerInferenceSuggestion] = {}

    for segment in segments:
        diarization_label = str(segment.get("speaker", "")).strip()
        if diarization_label not in allowed:
            continue

        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        for pattern, reason in SELF_INTRO_PATTERNS:
            candidate = _first_name_candidate(pattern, text)
            if candidate is None:
                continue

            resolved_name, attendee_signal = _match_candidate_to_attendees(
                candidate,
                meeting_context.attendees if meeting_context is not None else (),
            )
            signals = [reason]
            confidence = 0.97
            rationale = "Detected a self-introduction in the transcript."
            if attendee_signal is not None:
                signals.append(attendee_signal)
                confidence = 0.99
                rationale = (
                    "Detected a self-introduction in the transcript and matched it "
                    "to the linked meeting attendee list."
                )

            suggestion = SpeakerInferenceSuggestion(
                diarization_label=diarization_label,
                suggested_name=resolved_name,
                confidence=confidence,
                rationale=rationale,
                evidence_spans=(
                    SpeakerSuggestionEvidenceSpan(
                        quote=text,
                        reason=reason,
                        start_seconds=_normalize_optional_float(segment.get("start")),
                        end_seconds=_normalize_optional_float(segment.get("end")),
                    ),
                ),
                signals=tuple(signals),
                source="deterministic_rule",
            )

            current = best_by_label.get(diarization_label)
            if current is None or suggestion.confidence > current.confidence:
                best_by_label[diarization_label] = suggestion
            break

    return SpeakerInferenceResult(tuple(best_by_label.values()))


def build_mapping_based_speaker_suggestions(
    mapping: Mapping[str, str],
    *,
    segments: Sequence[dict[str, Any]],
    eligible_labels: Sequence[str],
    meeting_context: MeetingEventContext | None = None,
    source: str = "llm",
    embedding_similarity_scores: Mapping[str, float] | None = None,
) -> SpeakerInferenceResult:
    allowed = {label.strip() for label in eligible_labels if str(label).strip()}
    deterministic = {
        suggestion.diarization_label: suggestion
        for suggestion in detect_rule_based_speaker_suggestions(
            segments,
            eligible_labels,
            meeting_context,
        ).suggestions
    }
    suggestions: list[SpeakerInferenceSuggestion] = []

    for raw_label, raw_name in mapping.items():
        diarization_label = str(raw_label).strip()
        suggested_name = str(raw_name).strip()
        if not diarization_label or not suggested_name:
            continue
        if allowed and diarization_label not in allowed:
            continue

        deterministic_match = deterministic.get(diarization_label)
        if deterministic_match is not None and _names_are_compatible(
            deterministic_match.suggested_name,
            suggested_name,
        ):
            suggestions.append(
                SpeakerInferenceSuggestion(
                    diarization_label=deterministic_match.diarization_label,
                    suggested_name=suggested_name,
                    confidence=max(0.78, min(deterministic_match.confidence, 0.95)),
                    rationale=(
                        "The model suggestion is supported by direct transcript "
                        "self-identification evidence."
                    ),
                    evidence_spans=deterministic_match.evidence_spans,
                    signals=deterministic_match.signals,
                    source=source,
                )
            )
            continue

        evidence_spans = tuple(
            _find_transcript_name_mentions(
                diarization_label,
                suggested_name,
                segments,
            )
        )
        signals: list[str] = []
        sim_score = (
            float(embedding_similarity_scores.get(diarization_label, 0.0))
            if embedding_similarity_scores
            else 0.0
        )
        confidence = max(0.40, min(sim_score * 0.85, 0.70)) if sim_score > 0.0 else 0.50
        rationale = "Persisted as an evidence-backed AI suggestion for user review."

        if evidence_spans:
            signals.append("transcript_name_mention")
            confidence = 0.72
            rationale = (
                "Another speaker uses the suggested name in a turn next to this "
                "speaker's, as when addressing them."
            )

        attendee_signal = _attendee_match_signal(
            suggested_name,
            meeting_context.attendees if meeting_context is not None else (),
        )
        if attendee_signal is not None:
            signals.append(attendee_signal)
            confidence = max(confidence, 0.78 if evidence_spans else 0.64)
            rationale = (
                "The suggested name aligns with the linked meeting attendee list and is "
                "stored for user confirmation."
            )

        suggestions.append(
            SpeakerInferenceSuggestion(
                diarization_label=diarization_label,
                suggested_name=suggested_name,
                confidence=confidence,
                rationale=rationale,
                evidence_spans=evidence_spans,
                signals=tuple(dict.fromkeys(signals)),
                source=source,
            )
        )

    return SpeakerInferenceResult(tuple(suggestions))


def build_persisted_speaker_suggestion(
    suggestion: SpeakerInferenceSuggestion,
    *,
    origin: str,
    provider: str | None = None,
    recording_speaker_id: int | None = None,
    suggested_global_speaker_id: int | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = (created_at or datetime.now(UTC)).isoformat()
    return {
        "id": str(uuid4()),
        "diarization_label": suggestion.diarization_label,
        "recording_speaker_id": recording_speaker_id,
        "suggested_name": suggestion.suggested_name,
        "suggested_global_speaker_id": suggested_global_speaker_id,
        "confidence": round(float(suggestion.confidence), 4),
        "status": SPEAKER_SUGGESTION_STATUS_PENDING,
        "origin": origin,
        "source": suggestion.source,
        "provider": provider,
        "rationale": suggestion.rationale,
        "evidence_spans": [
            {
                "quote": span.quote,
                "reason": span.reason,
                "start_seconds": span.start_seconds,
                "end_seconds": span.end_seconds,
            }
            for span in suggestion.evidence_spans
        ],
        "signals": list(suggestion.signals),
        "created_at": timestamp,
        "updated_at": timestamp,
        "resolved_at": None,
        "resolution_reason": None,
        "resolution_actor_user_id": None,
    }


def load_transcript_speaker_suggestions(transcript: Any) -> list[dict[str, Any]]:
    raw_value = getattr(transcript, "speaker_name_suggestions", None)
    if not isinstance(raw_value, list):
        return []
    return [dict(item) for item in raw_value if isinstance(item, Mapping)]


def persist_transcript_speaker_suggestions(
    transcript: Any,
    new_suggestions: Sequence[dict[str, Any]],
    *,
    replaced_reason: str = "replaced_by_new_suggestion",
    actor_user_id: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    suggestions = load_transcript_speaker_suggestions(transcript)
    timestamp = (now or datetime.now(UTC)).isoformat()
    labels = {
        str(item.get("diarization_label", "")).strip()
        for item in new_suggestions
        if str(item.get("diarization_label", "")).strip()
    }

    if labels:
        for item in suggestions:
            if item.get("status") != SPEAKER_SUGGESTION_STATUS_PENDING:
                continue
            if str(item.get("diarization_label", "")).strip() not in labels:
                continue
            item["status"] = SPEAKER_SUGGESTION_STATUS_SUPERSEDED
            item["updated_at"] = timestamp
            item["resolved_at"] = timestamp
            item["resolution_reason"] = replaced_reason
            item["resolution_actor_user_id"] = actor_user_id

    suggestions.extend(dict(item) for item in new_suggestions)
    transcript.speaker_name_suggestions = suggestions
    return suggestions


def resolve_pending_transcript_speaker_suggestion(
    transcript: Any,
    *,
    diarization_label: str,
    resolution: str,
    actor_user_id: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    suggestions = load_transcript_speaker_suggestions(transcript)
    timestamp = (now or datetime.now(UTC)).isoformat()
    normalized_label = str(diarization_label).strip()

    for item in reversed(suggestions):
        if item.get("status") != SPEAKER_SUGGESTION_STATUS_PENDING:
            continue
        if str(item.get("diarization_label", "")).strip() != normalized_label:
            continue
        item["status"] = resolution
        item["updated_at"] = timestamp
        item["resolved_at"] = timestamp
        item["resolution_reason"] = reason
        item["resolution_actor_user_id"] = actor_user_id
        transcript.speaker_name_suggestions = suggestions
        return item

    return None


def supersede_pending_transcript_speaker_suggestions(
    transcript: Any,
    *,
    diarization_labels: Iterable[str],
    reason: str,
    actor_user_id: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    suggestions = load_transcript_speaker_suggestions(transcript)
    timestamp = (now or datetime.now(UTC)).isoformat()
    labels = {str(label).strip() for label in diarization_labels if str(label).strip()}
    changed: list[dict[str, Any]] = []

    if not labels:
        return changed

    for item in suggestions:
        if item.get("status") != SPEAKER_SUGGESTION_STATUS_PENDING:
            continue
        if str(item.get("diarization_label", "")).strip() not in labels:
            continue
        item["status"] = SPEAKER_SUGGESTION_STATUS_SUPERSEDED
        item["updated_at"] = timestamp
        item["resolved_at"] = timestamp
        item["resolution_reason"] = reason
        item["resolution_actor_user_id"] = actor_user_id
        changed.append(item)

    if changed:
        transcript.speaker_name_suggestions = suggestions
    return changed


def _read_required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise SpeakerSuggestionContractError(f"'{key}' must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise SpeakerSuggestionContractError(f"'{key}' must not be empty")
    return cleaned


def _read_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SpeakerSuggestionContractError(f"'{key}' must be a string when present")
    cleaned = value.strip()
    return cleaned or None


def _read_required_float(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise SpeakerSuggestionContractError(f"'{key}' must be numeric") from exc
    if not 0.0 <= numeric <= 1.0:
        raise SpeakerSuggestionContractError(f"'{key}' must be between 0.0 and 1.0")
    return numeric


def _read_string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SpeakerSuggestionContractError("'signals' must be an array when present")
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _read_evidence_spans(
    payload: Mapping[str, Any],
) -> tuple[SpeakerSuggestionEvidenceSpan, ...]:
    raw_evidence = payload.get("evidence_spans")
    if not isinstance(raw_evidence, list):
        raise SpeakerSuggestionContractError("'evidence_spans' must be an array")

    evidence_spans: list[SpeakerSuggestionEvidenceSpan] = []
    for item in raw_evidence:
        if not isinstance(item, Mapping):
            raise SpeakerSuggestionContractError(
                "each evidence span must be a JSON object"
            )
        evidence_spans.append(
            SpeakerSuggestionEvidenceSpan(
                quote=_read_required_string(item, "quote"),
                reason=_read_required_string(item, "reason"),
                start_seconds=_normalize_optional_float(item.get("start_seconds")),
                end_seconds=_normalize_optional_float(item.get("end_seconds")),
            )
        )
    return tuple(evidence_spans)


def _normalize_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _load_json_object(response_text: str) -> Mapping[str, Any]:
    text = response_text.strip()
    if not text:
        raise SpeakerSuggestionContractError("response_text must be a non-empty string")

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

    raise SpeakerSuggestionContractError(
        "Could not parse a speaker suggestion JSON object from the response"
    )


def _try_load_json_object(candidate: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        raise SpeakerSuggestionContractError(
            "speaker suggestion response must be a JSON object"
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


def _first_name_candidate(pattern: re.Pattern[str], text: str) -> str | None:
    """Return the first match of ``pattern`` that cleans to a plausible name.

    A rejected match ("I'm Sorry") does not end the search, so a later
    introduction in the same line ("I'm Sorry, I'm Priya") is still found.
    The search resumes inside the rejected name, not after it: without a
    comma the capture runs on into the next cue ("Sorry I'm Priya").
    """
    position = 0
    while (match := pattern.search(text, position)) is not None:
        candidate = _clean_candidate_name(match.group(1))
        if candidate and not is_placeholder_speaker_name(candidate):
            return candidate
        position = match.start(1) + 1
    return None


def _clean_candidate_name(candidate: str) -> str | None:
    cleaned = re.sub(r"^[^A-Za-z]+|[^A-Za-z'\-\s]+$", "", str(candidate).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or len(cleaned.split()) > 3:
        return None

    kept_tokens: list[str] = []
    for token in cleaned.split():
        lowered = token.lower()
        if (
            lowered in NAME_STOPWORDS
            or lowered in NON_NAME_WORDS
            or CONTRACTION_SUFFIX_PATTERN.search(token)
        ):
            break
        kept_tokens.append(token)
    cleaned = " ".join(kept_tokens).strip()
    if not cleaned:
        return None

    normalized_tokens = [token.lower() for token in cleaned.split()]
    if any(token in DISALLOWED_NAME_TOKENS for token in normalized_tokens):
        return None

    return " ".join(part.capitalize() for part in cleaned.split())


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _names_are_compatible(left: str, right: str) -> bool:
    left_normalized = _normalize_name(left)
    right_normalized = _normalize_name(right)
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized:
        return True

    left_tokens = left_normalized.split()
    right_tokens = right_normalized.split()
    return bool(left_tokens and right_tokens and left_tokens[0] == right_tokens[0])


def _match_candidate_to_attendees(
    candidate: str,
    attendees: Sequence[str],
) -> tuple[str, str | None]:
    normalized_candidate = _normalize_name(candidate)
    if not normalized_candidate:
        return candidate, None

    exact_matches = [
        attendee
        for attendee in attendees
        if _normalize_name(attendee) == normalized_candidate
    ]
    if exact_matches:
        return exact_matches[0], "meeting_attendee_exact"

    candidate_first = normalized_candidate.split()[0]
    first_name_matches = [
        attendee
        for attendee in attendees
        if _normalize_name(attendee).split()
        and _normalize_name(attendee).split()[0] == candidate_first
    ]
    if len(first_name_matches) == 1:
        return first_name_matches[0], "meeting_attendee_first_name"

    return candidate, None


def _attendee_match_signal(suggested_name: str, attendees: Sequence[str]) -> str | None:
    _, signal = _match_candidate_to_attendees(suggested_name, attendees)
    return signal


def _find_transcript_name_mentions(
    diarization_label: str,
    suggested_name: str,
    segments: Sequence[dict[str, Any]],
) -> list[SpeakerSuggestionEvidenceSpan]:
    """Find a line that addresses this speaker by the suggested name.

    A name used as an address term points at the adjacent *other* speaker:
    "thanks, Priya" at the previous one, "Priya, what do you think?" at the
    next one. So a mention counts only when it is spoken by someone else in the
    turn directly before or after one of this speaker's turns. The speaker's own
    mentions are never evidence: someone saying "thanks, Priya" is not Priya.
    Nor is a neighbour's self-introduction ("I'm Priya"): it names the speaker
    who said it, and SELF_INTRO_PATTERNS already credits them.
    """
    evidence: list[SpeakerSuggestionEvidenceSpan] = []
    # Case-sensitive, as the self-introduction rule is: names that are also
    # ordinary words ("I will send it", "mark it done") must not count.
    full_name_pattern = re.compile(rf"\b{re.escape(suggested_name)}\b")
    first_token = suggested_name.split()[0]
    first_name_pattern = re.compile(rf"\b{re.escape(first_token)}\b")
    labels = [str(segment.get("speaker", "")).strip() for segment in segments]

    for index, segment in enumerate(segments):
        if not labels[index] or labels[index] == diarization_label:
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if not (full_name_pattern.search(text) or first_name_pattern.search(text)):
            continue
        if _introduces_name(text, suggested_name):
            continue
        if diarization_label in _adjacent_other_speakers(labels, index):
            evidence.append(
                SpeakerSuggestionEvidenceSpan(
                    quote=text,
                    reason="transcript_name_mention",
                    start_seconds=_normalize_optional_float(segment.get("start")),
                    end_seconds=_normalize_optional_float(segment.get("end")),
                )
            )
            break

    return evidence


def _adjacent_other_speakers(labels: Sequence[str], index: int) -> set[str]:
    """Return the speakers of the turns directly before and after ``index``.

    Consecutive segments by the same speaker form one turn, so the search skips
    past them to the nearest segment by a different speaker on each side.
    """
    speaker = labels[index]
    adjacent: set[str] = set()
    for step in (-1, 1):
        position = index + step
        while 0 <= position < len(labels):
            label = labels[position]
            if label and label != speaker:
                adjacent.add(label)
                break
            position += step
    return adjacent


def _introduces_name(text: str, name: str) -> bool:
    """Whether ``text`` is a self-introduction by a name compatible with ``name``."""
    for pattern, _reason in SELF_INTRO_PATTERNS:
        candidate = _first_name_candidate(pattern, text)
        if candidate is not None and _names_are_compatible(candidate, name):
            return True
    return False
