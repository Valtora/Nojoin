"""The AI analytics tier's JSON contract: types, parsing, and evidence rules.

Three rules make this tier safe to attach to a named colleague, and all three
are enforced here rather than trusted to the prompt:

* **Speakers are an allowlist.** A name the model did not receive is a name it
  invented, so any item attributed to one is discarded.
* **Citations are verified, not accepted.** A quote must actually appear in the
  transcript and carry a timestamp inside the meeting. A sentiment or decision
  item left with no surviving citation is discarded, because an unfalsifiable
  claim about a person is the worst output this feature can produce.
* **Nothing is silently dropped.** Every discard is counted and reported, so a
  reading over two items is distinguishable from a reading over twenty.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .prompt import CONTESTED_LEADERSHIP

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

# Versions the analysis procedure, exactly as DELIVERY_METHOD_VERSION versions
# the delivery extraction and EMBEDDING_METHOD_VERSION versions a voiceprint.
# Bump it whenever the prompt, the schema, or the evidence rules change: an
# item produced by one procedure is not comparable with one produced by
# another, and the interface uses this to know what it is showing.
MEETING_ANALYSIS_METHOD_VERSION = 1

TONES = frozenset({"positive", "negative", "neutral", "mixed"})
CONSENSUS_KINDS = frozenset({"stated", "assumed", "none"})

# Slack on the meeting's own duration before a citation timestamp is treated as
# outside the recording. Durations are rounded from the media header and a
# model reading the last line of a transcript can land a second or two past it.
TIMESTAMP_TOLERANCE_SECONDS = 5.0

# Shortest normalised quote accepted as evidence. Below this a quote matches
# the transcript by coincidence rather than by being in it, so it proves
# nothing about the claim it is attached to.
MIN_QUOTE_CHARS = 6

MAX_TOPICS = 12
MAX_SENTIMENT_ITEMS = 24
MAX_QUESTIONS = 40
MAX_DECISIONS = 24
MAX_CITATIONS_PER_ITEM = 4


class MeetingAnalysisContractError(ValueError):
    """Raised when an AI analytics payload breaks the JSON contract."""


@dataclass
class ExclusionCounts:
    """What was thrown away, so the interface can say so.

    Anything excluded from this surface is counted rather than dropped, which
    is the same rule the deterministic tier follows for short turns and
    unmeasurable response gaps.
    """

    unknown_speaker_items: int = 0
    uncited_sentiment: int = 0
    uncited_decisions: int = 0
    unverifiable_citations: int = 0
    out_of_range_citations: int = 0
    malformed_items: int = 0
    ambiguous_speaker_names: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "unknown_speaker_items": self.unknown_speaker_items,
            "uncited_sentiment": self.uncited_sentiment,
            "uncited_decisions": self.uncited_decisions,
            "unverifiable_citations": self.unverifiable_citations,
            "out_of_range_citations": self.out_of_range_citations,
            "malformed_items": self.malformed_items,
            "ambiguous_speaker_names": self.ambiguous_speaker_names,
        }

    @property
    def total(self) -> int:
        return sum(self.as_dict().values())


@dataclass(frozen=True)
class SpeakerAllowlist:
    """The speakers the model may name, and how to resolve one back to a key.

    Two speakers resolving to the same display name are both withheld: the
    model's answer could mean either, and guessing would attach a claim to the
    wrong person. That is counted rather than hidden.
    """

    names: tuple[str, ...]
    _by_name: dict[str, str] = field(default_factory=dict, repr=False)
    _display: dict[str, str] = field(default_factory=dict, repr=False)
    ambiguous_count: int = 0

    def resolve(self, value: Any) -> str | None:
        """Speaker key for a name the model returned, or None if not allowed."""
        if not isinstance(value, str):
            return None
        return self._by_name.get(_normalise_name(value))

    def display_name(self, speaker_key: str) -> str:
        return self._display.get(speaker_key, speaker_key)


def build_speaker_allowlist(
    speakers: Sequence[Mapping[str, Any]],
) -> SpeakerAllowlist:
    """Build the allowlist from the deterministic tier's speaker directory."""
    by_name: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    order: list[tuple[str, str]] = []

    for speaker in speakers:
        key = str(speaker.get("speaker_key") or "").strip()
        name = str(speaker.get("name") or "").strip()
        if not key or not name:
            continue
        display[key] = name
        normalised = _normalise_name(name)
        by_name.setdefault(normalised, []).append(key)
        order.append((normalised, name))

    unique = {
        normalised: keys[0] for normalised, keys in by_name.items() if len(keys) == 1
    }
    ambiguous = sum(1 for keys in by_name.values() if len(keys) > 1)
    names = tuple(
        dict.fromkeys(name for normalised, name in order if normalised in unique)
    )
    return SpeakerAllowlist(
        names=names,
        _by_name=unique,
        _display=display,
        ambiguous_count=ambiguous,
    )


@dataclass(frozen=True)
class QuoteIndex:
    """The transcript's own words, for checking a quote was really said.

    Held as one normalised string rather than per utterance so a quote spanning
    a line break still verifies; the transcript's speaker and timestamp markers
    are excluded, so a model cannot satisfy the check by quoting them.
    """

    corpus: str = ""
    duration_seconds: float = 0.0

    def contains(self, quote: str) -> bool:
        normalised = _normalise_quote(quote)
        if len(normalised) < MIN_QUOTE_CHARS:
            return False
        return normalised in self.corpus

    def in_range(self, seconds: float) -> bool:
        if seconds < 0:
            return False
        if self.duration_seconds <= 0:
            return True
        return seconds <= self.duration_seconds + TIMESTAMP_TOLERANCE_SECONDS


def build_quote_index(
    utterance_texts: Iterable[str],
    duration_seconds: float,
) -> QuoteIndex:
    corpus = _normalise_quote(" ".join(str(text or "") for text in utterance_texts))
    return QuoteIndex(corpus=corpus, duration_seconds=max(float(duration_seconds), 0.0))


@dataclass(frozen=True)
class AnalysisCitation:
    quote: str
    start_seconds: float
    speaker_key: str | None = None


@dataclass(frozen=True)
class AnalysisTopic:
    title: str
    start_seconds: float
    end_seconds: float
    summary: str
    # None when the model named no one, or named someone not on the allowlist.
    led_by: str | None
    contested: bool
    leadership_basis: str | None


@dataclass(frozen=True)
class AnalysisSentiment:
    speaker_key: str
    tone: str
    summary: str
    citations: tuple[AnalysisCitation, ...]


@dataclass(frozen=True)
class AnalysisQuestion:
    question: str
    asked_by: str
    asked_at_seconds: float | None
    answered_by: str | None
    answered_at_seconds: float | None
    answer_summary: str | None


@dataclass(frozen=True)
class AnalysisDecision:
    decision: str
    proposed_by: str | None
    agreed_by: tuple[str, ...]
    objected_by: tuple[str, ...]
    consensus: str
    citations: tuple[AnalysisCitation, ...]


@dataclass(frozen=True)
class MeetingAnalysisResult:
    topics: tuple[AnalysisTopic, ...]
    sentiment: tuple[AnalysisSentiment, ...]
    questions: tuple[AnalysisQuestion, ...]
    decisions: tuple[AnalysisDecision, ...]
    excluded: ExclusionCounts


@dataclass(frozen=True)
class MeetingAnalysisRequest:
    """One meeting's analysis inputs, and the evidence rules for its response.

    The allowlist and the quote index travel with the request rather than being
    supplied at parse time so a backend cannot accidentally parse a response
    without them, which would admit invented speakers and unverifiable quotes.
    """

    transcript: str
    allowlist: SpeakerAllowlist
    quotes: QuoteIndex
    output_language_instruction: str | None = None

    def __post_init__(self) -> None:
        transcript = (self.transcript or "").strip()
        if not transcript:
            raise MeetingAnalysisContractError("transcript must be a non-empty string")
        if not self.allowlist.names:
            raise MeetingAnalysisContractError(
                "at least one uniquely named speaker is required"
            )
        object.__setattr__(self, "transcript", transcript)


def parse_meeting_analysis_response(
    response_text: str,
    *,
    request: MeetingAnalysisRequest,
) -> MeetingAnalysisResult:
    """Parse and police one AI analytics response.

    A malformed envelope raises; a malformed *item* is discarded and counted.
    The distinction matters because the first is a provider failure worth
    retrying and the second is a model that produced partly usable output.
    """
    allowlist = request.allowlist
    quotes = request.quotes
    payload = _load_payload(response_text)
    excluded = ExclusionCounts(ambiguous_speaker_names=allowlist.ambiguous_count)

    return MeetingAnalysisResult(
        topics=_read_topics(payload, allowlist, quotes, excluded),
        sentiment=_read_sentiment(payload, allowlist, quotes, excluded),
        questions=_read_questions(payload, allowlist, quotes, excluded),
        decisions=_read_decisions(payload, allowlist, quotes, excluded),
        excluded=excluded,
    )


def serialize_meeting_analysis_result(
    result: MeetingAnalysisResult,
) -> dict[str, Any]:
    """The stored shape. Speaker keys, never names: a rename must not orphan it."""
    return {
        "topics": [
            {
                "title": topic.title,
                "start_ms": _to_ms(topic.start_seconds),
                "end_ms": _to_ms(topic.end_seconds),
                "summary": topic.summary,
                "led_by": topic.led_by,
                "contested": topic.contested,
                "leadership_basis": topic.leadership_basis,
            }
            for topic in result.topics
        ],
        "sentiment": [
            {
                "speaker_key": item.speaker_key,
                "tone": item.tone,
                "summary": item.summary,
                "citations": [_serialize_citation(c) for c in item.citations],
            }
            for item in result.sentiment
        ],
        "questions": [
            {
                "question": item.question,
                "asked_by": item.asked_by,
                "asked_at_ms": _to_ms(item.asked_at_seconds),
                "answered_by": item.answered_by,
                "answered_at_ms": _to_ms(item.answered_at_seconds),
                "answer_summary": item.answer_summary,
            }
            for item in result.questions
        ],
        "decisions": [
            {
                "decision": item.decision,
                "proposed_by": item.proposed_by,
                "agreed_by": list(item.agreed_by),
                "objected_by": list(item.objected_by),
                "consensus": item.consensus,
                "citations": [_serialize_citation(c) for c in item.citations],
            }
            for item in result.decisions
        ],
        "excluded": result.excluded.as_dict(),
    }


def _serialize_citation(citation: AnalysisCitation) -> dict[str, Any]:
    return {
        "quote": citation.quote,
        "start_ms": _to_ms(citation.start_seconds),
        "speaker_key": citation.speaker_key,
    }


# --- section readers -------------------------------------------------------


def _read_topics(
    payload: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> tuple[AnalysisTopic, ...]:
    topics: list[AnalysisTopic] = []
    for item in _read_item_list(payload, "topics", MAX_TOPICS, excluded):
        topic = _read_topic(item, allowlist, quotes, excluded)
        if topic is not None:
            topics.append(topic)
    return tuple(sorted(topics, key=lambda topic: topic.start_seconds))


def _read_topic(
    item: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> AnalysisTopic | None:
    title = _clean_text(item.get("title"))
    summary = _clean_text(item.get("summary"))
    start = _read_seconds(item.get("start_seconds"))
    end = _read_seconds(item.get("end_seconds"))
    if not title or start is None or end is None or end <= start:
        excluded.malformed_items += 1
        return None
    if not quotes.in_range(start) or not quotes.in_range(end):
        excluded.out_of_range_citations += 1
        return None

    raw_leader = item.get("led_by")
    led_by = allowlist.resolve(raw_leader)
    contested = led_by is None and _is_contested(raw_leader)
    # A named leader who is not on the allowlist is an invention. The topic
    # itself is still real, so it survives without an attribution rather than
    # being discarded with one.
    if led_by is None and not contested and _clean_text(raw_leader):
        excluded.unknown_speaker_items += 1

    return AnalysisTopic(
        title=title,
        start_seconds=start,
        end_seconds=end,
        summary=summary or "",
        led_by=led_by,
        contested=contested,
        leadership_basis=_clean_text(item.get("leadership_basis")) or None,
    )


def _read_sentiment(
    payload: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> tuple[AnalysisSentiment, ...]:
    items: list[AnalysisSentiment] = []
    seen: set[str] = set()
    for raw in _read_item_list(payload, "sentiment", MAX_SENTIMENT_ITEMS, excluded):
        item = _read_sentiment_item(raw, allowlist, quotes, excluded)
        if item is None or item.speaker_key in seen:
            continue
        seen.add(item.speaker_key)
        items.append(item)
    return tuple(items)


def _read_sentiment_item(
    item: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> AnalysisSentiment | None:
    speaker_key = allowlist.resolve(item.get("speaker"))
    if speaker_key is None:
        excluded.unknown_speaker_items += 1
        return None

    tone = _clean_text(item.get("tone")).lower()
    summary = _clean_text(item.get("summary"))
    if tone not in TONES or not summary:
        excluded.malformed_items += 1
        return None

    citations = _read_citations(item, allowlist, quotes, excluded)
    # Mandatory, and deliberately so: a tone attached to a named colleague with
    # nothing to check it against is not a finding, it is an accusation.
    if not citations:
        excluded.uncited_sentiment += 1
        return None

    return AnalysisSentiment(
        speaker_key=speaker_key,
        tone=tone,
        summary=summary,
        citations=citations,
    )


def _read_questions(
    payload: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> tuple[AnalysisQuestion, ...]:
    items: list[AnalysisQuestion] = []
    for raw in _read_item_list(payload, "questions", MAX_QUESTIONS, excluded):
        item = _read_question(raw, allowlist, quotes, excluded)
        if item is not None:
            items.append(item)
    return tuple(items)


def _read_question(
    item: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> AnalysisQuestion | None:
    question = _clean_text(item.get("question"))
    asked_by = allowlist.resolve(item.get("asked_by"))
    if not question:
        excluded.malformed_items += 1
        return None
    if asked_by is None:
        excluded.unknown_speaker_items += 1
        return None

    answered_by = allowlist.resolve(item.get("answered_by"))
    if answered_by is None and _clean_text(item.get("answered_by")):
        # An answerer who is not on the allowlist leaves the question
        # unanswered rather than answered by nobody in particular.
        excluded.unknown_speaker_items += 1

    return AnalysisQuestion(
        question=question,
        asked_by=asked_by,
        asked_at_seconds=_read_bounded_seconds(item.get("asked_at_seconds"), quotes),
        answered_by=answered_by,
        answered_at_seconds=(
            _read_bounded_seconds(item.get("answered_at_seconds"), quotes)
            if answered_by
            else None
        ),
        answer_summary=(
            _clean_text(item.get("answer_summary")) or None if answered_by else None
        ),
    )


def _read_decisions(
    payload: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> tuple[AnalysisDecision, ...]:
    items: list[AnalysisDecision] = []
    for raw in _read_item_list(payload, "decisions", MAX_DECISIONS, excluded):
        item = _read_decision(raw, allowlist, quotes, excluded)
        if item is not None:
            items.append(item)
    return tuple(items)


def _read_decision(
    item: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> AnalysisDecision | None:
    decision = _clean_text(item.get("decision"))
    consensus = _clean_text(item.get("consensus")).lower()
    if not decision or consensus not in CONSENSUS_KINDS:
        excluded.malformed_items += 1
        return None

    citations = _read_citations(item, allowlist, quotes, excluded)
    # The same rule as sentiment, for the same reason. Naming who pushed back
    # against a colleague is the most consequential claim on this surface.
    if not citations:
        excluded.uncited_decisions += 1
        return None

    proposed_by = allowlist.resolve(item.get("proposed_by"))
    if proposed_by is None and _clean_text(item.get("proposed_by")):
        excluded.unknown_speaker_items += 1

    return AnalysisDecision(
        decision=decision,
        proposed_by=proposed_by,
        agreed_by=_read_speaker_list(item.get("agreed_by"), allowlist, excluded),
        objected_by=_read_speaker_list(item.get("objected_by"), allowlist, excluded),
        consensus=consensus,
        citations=citations,
    )


# --- shared readers --------------------------------------------------------


def _read_item_list(
    payload: Mapping[str, Any],
    key: str,
    limit: int,
    excluded: ExclusionCounts,
) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise MeetingAnalysisContractError(f"{key} must be an array")

    items: list[Mapping[str, Any]] = []
    for entry in value[:limit]:
        if isinstance(entry, Mapping):
            items.append(entry)
        else:
            excluded.malformed_items += 1
    excluded.malformed_items += max(len(value) - limit, 0)
    return items


def _read_citations(
    item: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> tuple[AnalysisCitation, ...]:
    value = item.get("citations")
    if not isinstance(value, list):
        return ()

    citations: list[AnalysisCitation] = []
    for entry in value[:MAX_CITATIONS_PER_ITEM]:
        if not isinstance(entry, Mapping) or len(citations) >= MAX_CITATIONS_PER_ITEM:
            continue
        citation = _read_citation(entry, allowlist, quotes, excluded)
        if citation is not None:
            citations.append(citation)
    return tuple(citations)


def _read_citation(
    entry: Mapping[str, Any],
    allowlist: SpeakerAllowlist,
    quotes: QuoteIndex,
    excluded: ExclusionCounts,
) -> AnalysisCitation | None:
    quote = _clean_text(entry.get("quote"))
    seconds = _read_seconds(entry.get("start_seconds"))
    if not quote or seconds is None:
        excluded.unverifiable_citations += 1
        return None
    if not quotes.in_range(seconds):
        excluded.out_of_range_citations += 1
        return None
    # The quote has to be in the transcript. A model that paraphrases inside
    # quotation marks produces something the user cannot check against the
    # audio, which is exactly what this tier must never do.
    if not quotes.contains(quote):
        excluded.unverifiable_citations += 1
        return None

    return AnalysisCitation(
        quote=quote,
        start_seconds=seconds,
        speaker_key=allowlist.resolve(entry.get("speaker")),
    )


def _read_speaker_list(
    value: Any,
    allowlist: SpeakerAllowlist,
    excluded: ExclusionCounts,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    keys: list[str] = []
    for entry in value:
        key = allowlist.resolve(entry)
        if key is None:
            excluded.unknown_speaker_items += 1
            continue
        if key not in keys:
            keys.append(key)
    return tuple(keys)


# --- primitives ------------------------------------------------------------


def _is_contested(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == CONTESTED_LEADERSHIP


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalise_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalise_quote(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", value.casefold()).strip()


def _read_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return None
    return seconds


def _read_bounded_seconds(value: Any, quotes: QuoteIndex) -> float | None:
    seconds = _read_seconds(value)
    if seconds is None or not quotes.in_range(seconds):
        return None
    return seconds


def _to_ms(seconds: float | None) -> int | None:
    if seconds is None:
        return None
    return int(round(seconds * 1000))


def _load_payload(response_text: str) -> Mapping[str, Any]:
    text = (response_text or "").strip()
    if not text:
        raise MeetingAnalysisContractError("response_text must be a non-empty string")

    direct = _try_load_json_object(text)
    if direct is not None:
        return direct

    for match in JSON_FENCE_PATTERN.finditer(text):
        fenced = _try_load_json_object(match.group(1).strip())
        if fenced is not None:
            return fenced

    inline = _try_extract_inline_json_object(text)
    if inline is not None:
        return inline

    raise MeetingAnalysisContractError(
        "Could not parse a meeting analysis JSON object from the response"
    )


def _try_load_json_object(candidate: str) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        raise MeetingAnalysisContractError(
            "Meeting analysis response must be a JSON object"
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
