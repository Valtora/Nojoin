"""The Analytics tab's AI section ("What was discussed") as export blocks.

This part is a model's reading of the transcript rather than a measurement,
and the tab keeps it on its own panel labelled "Read by AI, not measured" so it
is never mistaken for the figures above it. The export keeps the same label and
the same separation. Wording comes from MeetingAnalysisPanel.tsx and the Ai*
components beside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import theme
from .formatting import format_duration, format_timestamp
from .model import (
    LINE_BREAK,
    Block,
    Callout,
    CalloutTone,
    Heading,
    Inline,
    ListBlock,
    ListItem,
    Paragraph,
    Span,
    plain,
)

UNATTRIBUTED = "Unattributed"

TONE_LABELS: dict[str, tuple[str, str]] = {
    # tone: (label, status colour)
    "positive": ("Positive", "success"),
    "negative": ("Negative", "danger"),
    "neutral": ("Neutral", "neutral"),
    "mixed": ("Mixed", "warning"),
}

# "Assumed" must not read as a weaker kind of agreement: nobody agreed, they
# just did not object.
CONSENSUS_LABELS: dict[str, tuple[str, str]] = {
    "stated": ("Agreement stated", "success"),
    "assumed": ("Unchallenged", "warning"),
    "none": ("Not resolved", "danger"),
}


class _Speakers:
    """Resolve stored speaker keys to the names and colours used elsewhere."""

    def __init__(
        self, speakers: Sequence[Mapping[str, Any]], colors: Mapping[str, str]
    ):
        self._names = {speaker["speaker_key"]: speaker["name"] for speaker in speakers}
        self._colors = colors

    def name(self, key: str | None) -> str:
        return self._names.get(key or "", UNATTRIBUTED)

    def tagged(self, key: str | None) -> Inline:
        """The name with its colour dot, or the bare name for nobody."""
        color = self._colors.get(key or "")
        name = Span(self.name(key), bold=True)
        if not color or key not in self._names:
            return (name,)
        return (Span("● ", color=color), name)


def _notice(text: str, tone: CalloutTone = "neutral") -> Callout:
    return Callout(tone=tone, title=None, body=(Paragraph(plain(text)),))


def _subheading(text: str) -> Heading:
    return Heading(2, plain(text))


def _status_label(
    labels: Mapping[str, tuple[str, str]], value: str | None, fallback: str
) -> Span:
    label, status = labels.get(value or "", labels[fallback])
    return Span(f"[{label}]", bold=True, color=theme.STATUS_TEXT[status])


def _quotes(citations: Sequence[Mapping[str, Any]] | None) -> Inline:
    spans: list[Span] = []
    for citation in citations or []:
        spans.append(LINE_BREAK)
        if citation.get("start_ms") is not None:
            spans.append(
                Span(f"{format_timestamp(citation['start_ms'])}  ", color=theme.HELPER)
            )
        spans.append(Span(f"“{citation.get('quote') or ''}”", italic=True))
    return tuple(spans)


# -- the four subsections -----------------------------------------------------


def _topic_item(topic: Mapping[str, Any], people: _Speakers) -> ListItem:
    start, end = topic.get("start_ms"), topic.get("end_ms")
    spans: list[Span] = []
    if start is not None:
        spans.append(Span(f"{format_timestamp(start)}  ", color=theme.HELPER))
    spans.append(Span(topic.get("title") or "", bold=True))
    if start is not None and end is not None and end - start > 0:
        spans.append(Span(f"  {format_duration(end - start)}", color=theme.HELPER))
    if topic.get("summary"):
        spans += [LINE_BREAK, Span(topic["summary"])]
    spans.append(LINE_BREAK)
    if topic.get("contested"):
        # Deliberately not a name: two people driving a topic equally is a real
        # answer, and picking one of them would not be.
        spans.append(
            Span("Led jointly, or by no one in particular", color=theme.HELPER)
        )
    elif topic.get("led_by"):
        spans += [Span("Led by ", color=theme.HELPER), *people.tagged(topic["led_by"])]
        if topic.get("leadership_basis"):
            spans.append(Span(f" · {topic['leadership_basis']}", color=theme.HELPER))
    else:
        spans.append(Span("No clear lead", color=theme.HELPER))
    return ListItem(tuple(spans), ordered=True)


def topics_blocks(ai: Mapping[str, Any], people: _Speakers) -> tuple[Block, ...]:
    topics = ai.get("topics") or []
    body: Block = (
        ListBlock(tuple(_topic_item(topic, people) for topic in topics))
        if topics
        else Paragraph(
            plain("No distinct topics were identified in this meeting."), tone="muted"
        )
    )
    return (_subheading("What the meeting covered"), body)


def _question_item(question: Mapping[str, Any], people: _Speakers) -> ListItem:
    spans: list[Span] = [Span(question.get("question") or "", bold=True), LINE_BREAK]
    spans += [
        Span("Asked by ", color=theme.HELPER),
        *people.tagged(question.get("asked_by")),
    ]
    if question.get("asked_at_ms") is not None:
        spans.append(
            Span(f" · {format_timestamp(question['asked_at_ms'])}", color=theme.HELPER)
        )
    spans.append(LINE_BREAK)
    if question.get("answered_by"):
        spans.append(
            Span(f"{people.name(question['answered_by'])} answered", bold=True)
        )
        summary = question.get("answer_summary")
        spans.append(Span(f": {summary}" if summary else "."))
    else:
        spans.append(Span("Unanswered", bold=True, color=theme.STATUS_TEXT["warning"]))
    return ListItem(tuple(spans))


def questions_blocks(ai: Mapping[str, Any], people: _Speakers) -> tuple[Block, ...]:
    questions = ai.get("questions") or []
    if not questions:
        return (
            _subheading("Questions asked"),
            Paragraph(
                plain("No substantive questions were identified in this meeting."),
                tone="muted",
            ),
        )
    # Unanswered questions lead: they are the reason to read this section.
    ordered = [q for q in questions if not q.get("answered_by")] + [
        q for q in questions if q.get("answered_by")
    ]
    unanswered = len(questions) - sum(1 for q in questions if q.get("answered_by"))
    blocks: list[Block] = [_subheading("Questions asked")]
    if unanswered:
        noun = "question" if len(questions) == 1 else "questions"
        blocks.append(
            Paragraph(
                plain(
                    f"{unanswered} of {len(questions)} {noun} went unanswered.",
                    bold=True,
                )
            )
        )
    blocks.append(ListBlock(tuple(_question_item(q, people) for q in ordered)))
    return tuple(blocks)


def sentiment_blocks(ai: Mapping[str, Any], people: _Speakers) -> tuple[Block, ...]:
    items = ai.get("sentiment") or []
    if not items:
        return (
            _subheading("How people put things"),
            Paragraph(
                plain(
                    "Nobody's words carried a clear enough position, with a quote to support "
                    "it, to report here."
                ),
                tone="muted",
            ),
        )
    entries = tuple(
        ListItem(
            people.tagged(item.get("speaker_key"))
            + (Span("  "), _status_label(TONE_LABELS, item.get("tone"), "neutral"))
            + ((LINE_BREAK, Span(item["summary"])) if item.get("summary") else ())
            + _quotes(item.get("citations"))
        )
        for item in items
    )
    return (
        _subheading("How people put things"),
        ListBlock(entries),
        Paragraph(
            plain(
                "Read from what people said, not from how they sounded. Nojoin does not detect "
                "emotion, and this is not combined with the measured delivery figures above: a "
                "person can make a positive point flatly, or a negative one warmly."
            ),
            tone="small",
        ),
    )


def _decision_item(decision: Mapping[str, Any], people: _Speakers) -> ListItem:
    spans: list[Span] = [
        Span(decision.get("decision") or "", bold=True),
        Span("  "),
        _status_label(CONSENSUS_LABELS, decision.get("consensus"), "assumed"),
    ]
    if decision.get("proposed_by"):
        spans += [
            LINE_BREAK,
            Span("Proposed by ", color=theme.HELPER),
            *people.tagged(decision["proposed_by"]),
        ]
    for label, keys in (
        ("Agreed", decision.get("agreed_by")),
        ("Pushed back", decision.get("objected_by")),
    ):
        if keys:
            spans += [
                LINE_BREAK,
                Span(f"{label}: ", color=theme.HELPER),
                Span(", ".join(people.name(key) for key in keys)),
            ]
    spans += _quotes(decision.get("citations"))
    return ListItem(tuple(spans))


def decisions_blocks(ai: Mapping[str, Any], people: _Speakers) -> tuple[Block, ...]:
    decisions = ai.get("decisions") or []
    if not decisions:
        return (
            _subheading("Who owned each decision"),
            Paragraph(
                plain(
                    "No decisions with quotable ownership were identified. The meeting notes "
                    "remain the record of what was decided."
                ),
                tone="muted",
            ),
        )
    return (
        _subheading("Who owned each decision"),
        ListBlock(tuple(_decision_item(decision, people) for decision in decisions)),
        Paragraph(
            plain(
                "This describes who owned each decision, not what was decided: the meeting "
                "notes are the record of that. Only speakers whose own words show it are listed "
                "as agreeing or pushing back, and every entry carries a quote. Unchallenged "
                "means nobody objected, which is not the same as agreement."
            ),
            tone="small",
        ),
    )


# -- the section ----------------------------------------------------------------


def _absent_blocks(analytics: Mapping[str, Any]) -> tuple[Block, ...] | None:
    status = analytics.get("ai_status") or "pending"
    if status == "generating":
        return (
            _notice(
                "This meeting was being analysed when the file was exported, so it is not included."
            ),
        )
    if status == "unavailable":
        return (
            _notice(
                "This needs an AI provider, and none is configured for this installation. "
                "Everything else in this section is measured from the recording and works "
                "without one."
            ),
        )
    if status == "error":
        return (
            _notice(
                analytics.get("ai_error_message")
                or "This meeting could not be analysed."
            ),
        )
    if not analytics.get("ai") or status != "completed":
        return (
            _notice(
                "Not analysed. Use Analyse meeting on the Analytics tab to add the topics the "
                "meeting moved through, how people put things, which questions went unanswered "
                "and who owned each decision."
            ),
        )
    return None


def _caveats(analytics: Mapping[str, Any], ai: Mapping[str, Any]) -> tuple[Block, ...]:
    blocks: list[Block] = []
    if analytics.get("ai_stale"):
        blocks.append(
            _notice("The transcript has changed since this was written.", "warning")
        )
    if ai.get("transcript_truncated"):
        through = format_timestamp(ai.get("analysed_through_ms") or 0)
        blocks.append(
            _notice(
                "This meeting was too long to analyse in full. Everything below covers the "
                f"meeting up to {through} only.",
                "warning",
            )
        )
    return tuple(blocks)


def ai_blocks(
    analytics: Mapping[str, Any], colors: Mapping[str, str]
) -> tuple[Block, ...]:
    heading: tuple[Block, ...] = (
        Heading(1, plain("What was discussed")),
        Paragraph(
            (
                Span("Read by AI, not measured. ", bold=True),
                Span(
                    "Read from the transcript by AI, with quotes you can check. Everything "
                    "above is measured; this is not."
                ),
            ),
            tone="muted",
        ),
    )
    absent = _absent_blocks(analytics)
    if absent is not None:
        return heading + absent
    ai = analytics["ai"]
    people = _Speakers(analytics["speakers"], colors)
    return (
        heading
        + _caveats(analytics, ai)
        + topics_blocks(ai, people)
        + questions_blocks(ai, people)
        + sentiment_blocks(ai, people)
        + decisions_blocks(ai, people)
        + (
            Paragraph(
                plain(
                    "Unlike the rest of this section, this part is an AI model's reading of the "
                    "transcript rather than a measurement, so treat it as a starting point and "
                    "check the quotes. Anything it could not evidence with a quote from the "
                    "transcript was discarded rather than shown."
                ),
                tone="small",
            ),
        )
    )
