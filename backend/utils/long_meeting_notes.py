"""Meeting notes for a transcript longer than the model's context window.

When a meeting's notes prompt does not fit the window, and no secondary
provider produced the notes instead, the transcript is split at utterance
boundaries into parts sized to the window. Notes are drafted for each part, and
the drafts are combined into one set of notes. Drafts too long to combine in
one call are combined in consecutive groups first, then the groups again, until
one call can take them all.

The automatic pipeline's single call also returns a title and speaker
suggestions. In parts, a speaker who introduced themselves in the transcript is
named by that introduction. For the others, each draft names the unresolved
speakers its part gives evidence for. A label every part named alike keeps that
name; for a label the parts named differently, the final combining call picks
one of the names they gave or none, and cannot add a name of its own. The final call also writes the
title. Drafts leave unresolved labels as they are and the names are put into
the finished notes afterwards, so two parts that disagree about a speaker
cannot leave two names for one person in the notes.

Nothing here is specific to a provider. The caller supplies ``complete``, which
sends one prompt and returns the answer, raising ContextWindowExceededError
when the prompt or its answer did not fit, and a PartsBudget measured from the
refusal of the whole prompt. Only the Ollama backend uses it.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, TypeVar

from backend.utils.context_window import ContextWindowExceededError
from backend.utils.languages import build_output_language_prompt_section
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
    MeetingIntelligenceContractError,
    build_title_preference_instruction,
    finalise_automatic_meeting_intelligence_result,
)
from backend.utils.meeting_notes import (
    MeetingEventContext,
    NotesPromptContext,
    build_documents_prompt_section,
    build_glossary_prompt_section,
    build_meeting_context_prompt_section,
    build_meeting_metadata_prompt_section,
    build_notes_body_spec,
    build_user_notes_prompt_section,
    is_placeholder_speaker_name,
    strip_leading_title_heading,
)
from backend.utils.personas import tone_block
from backend.utils.prompt_blocks import PromptBlock, render_prompt_blocks
from backend.utils.speaker_name_suggestions import detect_rule_based_speaker_suggestions
from backend.utils.transcript_parts import (
    TIMESTAMP_PATTERN,
    TranscriptPart,
    split_transcript,
    utf8_size,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Sends one prompt and returns the answer.
Complete = Callable[[str], str]

# Room left in the window for a draft's answer: a quarter of it, at most this.
DRAFT_ANSWER_MAX_TOKENS = 8192
# Room left for a combining call's answer, which is the notes for a longer
# stretch of the meeting: a third of the window, at most this.
MERGE_ANSWER_MAX_TOKENS = 16384
# Combining calls hold notes and instructions rather than transcript, so they
# are priced as prose. Notes with tables and SPEAKER_00 labels ran denser than
# 3 bytes a token on qwen2.5; a call the price lets through and Ollama refuses
# is split.
PROSE_BYTES_PER_TOKEN = 2.5
# Drafts are asked to be short enough that about this many share a combining
# call, so the notes of a long meeting combine in a few rounds.
DRAFTS_PER_MERGE = 3
# A part smaller than this is not split again when its draft does not fit: the
# window is then too small for the rest of the prompt, not for the part.
MIN_PART_BYTES = 2048


@dataclass(frozen=True)
class PartsBudget:
    """The window, and what a byte costs in tokens.

    ``tokens_per_byte`` prices a draft's prompt, which is mostly transcript,
    and comes from the caller. ``prose_tokens_per_byte`` prices combining
    calls, which hold notes and instructions.
    """

    window: int
    tokens_per_byte: float
    prose_tokens_per_byte: float = 1 / PROSE_BYTES_PER_TOKEN

    def tokens(self, text: str) -> int:
        return math.ceil(utf8_size(text) * self.tokens_per_byte)

    def bytes_for(self, tokens: int) -> int:
        return max(0, int(tokens / self.tokens_per_byte))

    def prose_tokens(self, text: str) -> int:
        return math.ceil(utf8_size(text) * self.prose_tokens_per_byte)

    @property
    def draft_answer_tokens(self) -> int:
        return min(self.window // 4, DRAFT_ANSWER_MAX_TOKENS)

    @property
    def merge_answer_tokens(self) -> int:
        return min(self.window // 3, MERGE_ANSWER_MAX_TOKENS)


# --- Prompts ---------------------------------------------------------------

PART_INTRO = """You are an expert meeting-notes assistant. This meeting is too long to read in one pass, so its transcript has been split into consecutive parts. Write notes for the one part given below. A later step combines the notes of every part into the notes for the whole meeting, working only from what the notes of each part say."""

USE_SPEAKER_MAPPING = """Use the speaker mapping to refer to participants by their inferred names or roles instead of generic labels."""

PART_INTRO_SPEAKERS = """You are an expert meeting-notes assistant. This meeting is too long to read in one pass, so its transcript has been split into consecutive parts. For the one part given below, write meeting notes in Markdown, then name the unresolved speakers this part gives evidence for. A later step combines the notes of every part into the notes for the whole meeting, working only from what the notes of each part say."""

PART_SPEAKER_RULES = """- Treat any non-generic speaker names already present in the transcript as trusted.
- In the notes, write every unresolved label exactly as the transcript does, such as SPEAKER_03. Names are put in after the parts are combined.
- After the notes, list each unresolved label this part gives evidence for, such as someone introducing themselves or being addressed by name, one per line between <speaker_names> tags as below. Give the person's name on its own when the meeting gave one, and otherwise their role. Leave out a label you are not confident about, and leave the tags empty when there is none.

<speaker_names>
SPEAKER_00: Person name or role
</speaker_names>"""

# The example names above. A model sometimes copies them back as an answer.
EXAMPLE_NAMES = {"person name or role", "one of the names listed for speaker_00"}

PART_CLOSING = """Return only the notes for this part, starting directly with the first section and with no preamble or closing commentary."""

PART_CLOSING_SPEAKERS = """Return only the notes for this part, starting directly with the first section, followed by the <speaker_names> block."""

MERGE_INTRO = """You are an expert meeting-notes assistant. A long meeting was split into consecutive parts, and notes were written for each part. Combine the notes of the parts below into one set of notes."""

MERGE_INTRO_TITLE = """You are an expert meeting-notes assistant. A long meeting was split into consecutive parts, and notes were written for each part. From the notes of the parts below, write a title for the meeting and one set of notes for the whole meeting."""

MERGE_TITLE_RULES = """- Start the answer with the title on its own line between <title> tags, as in <title>Quarterly Budget Review</title>, then the notes in Markdown.
- The title and the notes must reflect the same meeting interpretation."""

MERGE_LABELS_KEPT = """Leave every diarization label, such as SPEAKER_03, exactly as the notes of the parts write it. Names are put in afterwards."""

SETTLE_SPEAKERS = """The parts named these diarization labels differently. After the notes, give the one name the notes of the parts best support for each, copied exactly from its list, one per line between <speaker_names> tags as below, and prefer a name the meeting itself gave (someone introducing themselves or being addressed by name) over a role. Leave a label out when no name on its list is well supported, and name no other label.

<speaker_names>
SPEAKER_00: One of the names listed for SPEAKER_00
</speaker_names>"""

MERGE_CLOSING = """Return only the combined notes, starting directly with the first section and with no preamble or closing commentary."""

MERGE_CLOSING_TITLE = """Return only the <title> line and then the notes, starting with their first section, with no preamble or closing commentary."""

MERGE_CLOSING_TITLE_SPEAKERS = """Return only the <title> line, then the notes starting with their first section, then the <speaker_names> block, with no preamble or closing commentary."""


@dataclass(frozen=True)
class LongMeetingContext:
    """The prompt sections every call for one meeting shares.

    ``request`` is set for the automatic pipeline, whose drafts also name
    speakers and whose final call also writes the title; Regenerate Notes
    leaves it None. Every answer is Markdown, with any names and the title in
    tagged lines (see ``_split_tagged``): a small model held to JSON output
    over dozens of calls loops on a token, and Ollama aborts the answer.
    """

    speaker_mapping: str
    metadata: str
    glossary: str
    user_notes: str
    meeting_context: str
    documents: str
    language: str
    notes_spec: str
    request: Optional[AutomaticMeetingIntelligenceRequest] = None
    # A holiday persona's notes tone, as the whole-meeting prompt carries it.
    tone: Optional[str] = None


def context_for_notes(  # noqa: PLR0913 - one argument per prompt section
    speaker_mapping_table: str,
    user_notes: Optional[str],
    meeting_context: Optional[MeetingEventContext],
    output_language_instruction: Optional[str],
    notes_context: Optional[NotesPromptContext],
) -> LongMeetingContext:
    """The sections of a Regenerate Notes prompt, as LLMBackend.build_notes_prompt writes them."""
    notes_context = notes_context or NotesPromptContext()
    return LongMeetingContext(
        speaker_mapping=speaker_mapping_table,
        metadata=build_meeting_metadata_prompt_section(notes_context.metadata),
        glossary=build_glossary_prompt_section(notes_context.glossary),
        user_notes=build_user_notes_prompt_section(user_notes),
        meeting_context=build_meeting_context_prompt_section(meeting_context),
        documents=build_documents_prompt_section(notes_context.documents),
        language=build_output_language_prompt_section(output_language_instruction),
        notes_spec=build_notes_body_spec(notes_context.notes_sections),
        tone=notes_context.tone,
    )


def context_for_request(
    request: AutomaticMeetingIntelligenceRequest,
) -> LongMeetingContext:
    """The sections of the automatic pipeline's unified prompt."""
    return LongMeetingContext(
        speaker_mapping="",
        metadata=build_meeting_metadata_prompt_section(request.meeting_metadata),
        glossary=build_glossary_prompt_section(request.glossary),
        user_notes=build_user_notes_prompt_section(request.user_notes),
        meeting_context=build_meeting_context_prompt_section(request.meeting_context),
        documents=build_documents_prompt_section(request.documents),
        language=build_output_language_prompt_section(
            request.output_language_instruction
        ),
        notes_spec=build_notes_body_spec(request.notes_sections),
        request=request,
        tone=request.notes_only_tone,
    )


def _shared_blocks(
    context: LongMeetingContext, *, documents: bool
) -> list[PromptBlock]:
    blocks: list[PromptBlock] = [
        ("# Recording Metadata", context.metadata),
        ("# Glossary", context.glossary),
        ("# User Notes Context", context.user_notes),
        ("# Meeting Context", context.meeting_context),
    ]
    if documents:
        blocks.append(("# Attached Documents", context.documents))
    # Carries its own heading.
    blocks.append((None, context.language))
    return blocks


def _part_instructions(
    index: int, total: int, part: TranscriptPart, words: Optional[int]
) -> str:
    span = (
        f", from {part.start} to {part.end} in the recording"
        if part.start and part.end
        else ""
    )
    length = (
        f"- Write at most {words:,} words, so the notes of every part fit "
        "together when they are combined.\n"
        if words
        else ""
    )
    return (
        f"This is part {index} of {total}{span}.\n"
        "- Follow the notes format above, but write the summary for this part "
        "alone, and do not guess at what was said before or after it.\n"
        f"{length}"
        "- Keep every decision and action item in this part, with its owner, "
        "deadline and figures, and shorten the discussion before dropping any "
        "of them. The final notes are built from these notes alone, so "
        "anything left out here is lost.\n"
        "- Where this part opens or ends partway through a discussion, record "
        "the discussion as it stands and leave its outcome open."
    )


def _labels_section(labels: Sequence[str]) -> str:
    lines = ["Only these diarization labels may be named:"]
    lines.extend(f"- {label}" for label in labels)
    return "\n".join(lines)


def build_part_prompt(
    context: LongMeetingContext,
    index: int,
    total: int,
    part: TranscriptPart,
    words: Optional[int] = None,
) -> str:
    """The prompt for one part's draft.

    Everything before the part's own instructions is the same for every part
    of the meeting, so a server that reuses a cached prompt prefix reads it
    once.
    """
    request = context.request
    labels = request.unresolved_speakers if request is not None else ()
    if request is None:
        head: list[PromptBlock] = [
            (None, f"{PART_INTRO} {USE_SPEAKER_MAPPING}"),
            ("# Speaker Mapping", context.speaker_mapping),
        ]
    elif labels:
        head = [
            (None, PART_INTRO_SPEAKERS),
            ("# Speaker Rules", PART_SPEAKER_RULES),
            ("# Unresolved Speaker Labels", _labels_section(labels)),
        ]
    else:
        head = [(None, PART_INTRO)]
    closing = PART_CLOSING_SPEAKERS if labels else PART_CLOSING
    return render_prompt_blocks(
        [
            *head,
            *_shared_blocks(context, documents=True),
            ("# Notes Format", context.notes_spec),
            tone_block(context.tone),
            ("# This Part", _part_instructions(index, total, part, words)),
            ("# Transcript Part", part.text),
            (None, _closing(closing, words)),
        ]
    )


@dataclass(frozen=True)
class _Notes:
    """Notes covering parts ``first`` to ``last``, and the times they span."""

    text: str
    first: int
    last: int
    start: Optional[str]
    end: Optional[str]


def _describe(items: Sequence[_Notes], total: int) -> str:
    first, last = items[0].first, items[-1].last
    parts = (
        f"part {first} of {total}"
        if first == last
        else f"parts {first} to {last} of {total}"
    )
    start, end = items[0].start, items[-1].end
    return f"{parts}, from {start} to {end}" if start and end else parts


def _combining_rules(scope: str, words: Optional[int]) -> str:
    length = (
        f"- Write at most {words:,} words. Where the notes of the parts hold "
        "more than that allows, shorten the discussion and key points first, "
        "and keep every decision and action item.\n"
        if words
        else ""
    )
    return (
        f"- Write one set of notes for {scope}, following the notes format "
        f"above. The summary gives one account of {scope}, not a summary of "
        "each part in turn.\n"
        f"{length}"
        "- Where the same item appears in more than one part, such as an action "
        "item raised twice, write it once, keeping the most specific owner, "
        "deadline and figure.\n"
        "- Keep every decision and action item in the notes of the parts, with "
        "its owner, deadline and figures, and add nothing they do not contain.\n"
        "- Keep topics in the order they came up."
    )


def _closing(markdown: str, words: Optional[int]) -> str:
    """The last line of a prompt, which repeats the length a model reads last."""
    if not words:
        return markdown
    return f"{markdown} Write at most {words:,} words."


def build_merge_prompt(  # noqa: PLR0913 - the merge's scope, length and speakers
    context: LongMeetingContext,
    items: Sequence[_Notes],
    total: int,
    *,
    final: bool,
    words: Optional[int] = None,
    disputed: Optional[Mapping[str, Sequence[str]]] = None,
) -> str:
    """The prompt that combines the notes of consecutive parts.

    Attached documents are left out: the drafts already drew on them, and the
    room is needed for the drafts. The final call of the automatic pipeline
    also writes the title and settles the ``disputed`` speaker labels from the
    names the parts gave them.
    """
    request = context.request
    scope = (
        "the whole meeting"
        if final
        else f"this stretch of the meeting ({_describe(items, total)})"
    )
    speakers: PromptBlock = (
        ("# Speaker Mapping", context.speaker_mapping)
        if request is None
        else (
            "# Unresolved Speaker Labels",
            MERGE_LABELS_KEPT if request.unresolved_speakers else None,
        )
    )
    part_notes = "\n\n".join(
        f'<part_notes covering="{_describe([item], total)}">\n'
        f"{item.text.strip()}\n</part_notes>"
        for item in items
    )
    if final and request is not None:
        settle = "\n".join(
            f"- {label}: {'; '.join(names)}"
            for label, names in (disputed or {}).items()
        )
        head: list[PromptBlock] = [
            (None, MERGE_INTRO_TITLE),
            ("# Title Rules", MERGE_TITLE_RULES),
            (
                "# Title Style",
                build_title_preference_instruction(request.prefer_short_titles),
            ),
            speakers,
            (
                "# Speaker Names To Settle",
                f"{SETTLE_SPEAKERS}\n\nThe names each label was given:\n{settle}"
                if settle
                else None,
            ),
        ]
        closing = _closing(
            MERGE_CLOSING_TITLE_SPEAKERS if settle else MERGE_CLOSING_TITLE, words
        )
    else:
        head = [(None, MERGE_INTRO), speakers]
        closing = _closing(MERGE_CLOSING, words)
    return render_prompt_blocks(
        [
            *head,
            *_shared_blocks(context, documents=False),
            ("# Notes Format", context.notes_spec),
            tone_block(context.tone),
            ("# Combining Rules", _combining_rules(scope, words)),
            ("# Notes of the Parts", part_notes),
            (None, closing),
        ]
    )


# --- Reading answers -------------------------------------------------------


def _clean_notes(notes: str) -> str:
    return strip_leading_title_heading(notes.replace("\r\n", "\n").strip())


def _read_markdown(answer: str) -> str:
    notes = _clean_notes(answer)
    if not notes:
        raise MeetingIntelligenceContractError("the answer contained no notes")
    return notes


_TAGGED_TITLE = re.compile(r"<title>\s*(.*?)\s*</title>", re.IGNORECASE | re.DOTALL)
_SPEAKER_BLOCK = re.compile(
    r"<speaker_names>(.*?)(?:</speaker_names>|\Z)", re.IGNORECASE | re.DOTALL
)
_SPEAKER_LINE = re.compile(r"^[\s>*-]*([A-Za-z][\w-]*)\s*:\s*(.+?)\s*$", re.MULTILINE)
_NO_NAME = {"none", "unknown", "n/a", "-", *EXAMPLE_NAMES}


def _split_tagged(answer: str) -> tuple[str, dict[str, str]]:
    """The answer without its <speaker_names> block, and the names in it."""
    match = _SPEAKER_BLOCK.search(answer)
    if not match:
        return answer, {}
    names: dict[str, str] = {}
    for label, name in _SPEAKER_LINE.findall(match.group(1)):
        name = name.strip(" \"'`*")
        if name and name.casefold() not in _NO_NAME:
            names[label] = name
    return answer[: match.start()] + answer[match.end() :], names


def _read_draft(answer: str) -> tuple[dict[str, str], str]:
    text, names = _split_tagged(answer)
    return names, _read_markdown(text)


def _title_and_notes_reader(
    request: AutomaticMeetingIntelligenceRequest,
) -> Callable[[str], tuple[str, str, dict[str, str]]]:
    def read(answer: str) -> tuple[str, str, dict[str, str]]:
        text, names = _split_tagged(answer)
        match = _TAGGED_TITLE.search(text)
        if match:
            title = match.group(1)
            text = text[: match.start()] + text[match.end() :]
        else:
            # A model that drops the tags tends to open with a "# Title" line.
            lines = text.strip().splitlines()
            if not lines or not lines[0].startswith("# "):
                raise MeetingIntelligenceContractError("the answer gave no title")
            title = lines[0][2:]
            text = "\n".join(lines[1:])
        # Built to hold the answer to the unified call's own contract.
        checked = AutomaticMeetingIntelligenceResult(
            speaker_mapping={},
            title=title,
            notes_markdown=_clean_notes(text),
            require_section_heading=not request.uses_custom_notes_sections,
        )
        return checked.title, checked.notes_markdown, names

    return read


def _name_words(name: str) -> set[str]:
    return set(re.findall(r"\w+", name.casefold()))


@dataclass(frozen=True)
class SpeakerNames:
    """The names the parts gave unresolved labels.

    ``agreed`` holds a label every part that named it named alike; ``disputed``
    holds each other label's distinct names, in the order the parts gave them.
    """

    agreed: dict[str, str]
    disputed: dict[str, tuple[str, ...]]

    def settle(self, chosen: Mapping[object, object]) -> dict[str, str]:
        """The agreed names, plus each disputed label's name in ``chosen``.

        A choice counts only when it is one of that label's names, so the final
        call can pick between the parts' suggestions but never add its own.
        """
        settled = dict(self.agreed)
        for label, answer in chosen.items():
            names = {n.casefold(): n for n in self.disputed.get(str(label), ())}
            # Exactly one listed name, or no choice at all: a model that copies
            # the list back has not chosen, and a chosen name is applied to the
            # speaker without review, so a wrong one costs more than none.
            match = names.get(" ".join(str(answer).split()).casefold())
            if match:
                settled[str(label)] = match
        return settled

    def with_introductions(self, introduced: Mapping[str, str]) -> "SpeakerNames":
        """These names, with each self-introduced label settled by its introduction.

        A speaker who said their own name is better evidence than any draft's
        reading of a long part, which can attach a name to the wrong label. An
        agreed name that contains the introduced one ("Ana Lima" for "Ana") is
        kept as the fuller form.
        """
        agreed = {
            label: name
            for label, name in self.agreed.items()
            if label not in introduced
        }
        for label, name in introduced.items():
            current = self.agreed.get(label)
            fuller = current and _name_words(name) <= _name_words(current)
            agreed[label] = current if fuller else name
        disputed = {
            label: names
            for label, names in self.disputed.items()
            if label not in introduced
        }
        return SpeakerNames(agreed=agreed, disputed=disputed)


_TRANSCRIPT_LINE = re.compile(
    rf"^\[({TIMESTAMP_PATTERN})(?:\s*-\s*({TIMESTAMP_PATTERN}))?\]\s*(.+?)(?: \(with [^)]*\))?: (.*)$"
)


def _seconds(timestamp: str) -> float:
    seconds = 0.0
    for field in timestamp.split(":"):
        seconds = seconds * 60 + int(field)
    return seconds


def introduced_names(
    transcript: str,
    labels: Sequence[str],
    meeting_context: Optional[MeetingEventContext],
) -> dict[str, str]:
    """Names unresolved speakers gave themselves in the transcript, by label.

    The same self-introduction rules as a run without AI, read from the
    transcript's lines.
    """
    segments = []
    for line in transcript.splitlines():
        match = _TRANSCRIPT_LINE.match(line)
        if match:
            start, end, speaker, text = match.groups()
            segments.append(
                {
                    "start": _seconds(start),
                    "end": _seconds(end or start),
                    "speaker": speaker,
                    "text": text,
                }
            )
    found = detect_rule_based_speaker_suggestions(segments, labels, meeting_context)
    return {s.diarization_label: s.suggested_name for s in found.suggestions}


def reconcile_speaker_mappings(
    mappings: Sequence[Mapping[str, str]], eligible_labels: Sequence[str]
) -> SpeakerNames:
    """Sort the parts' names for each label into agreed and disputed.

    Two names agree when the words of one are all in the other ("Ana" and "Ana
    Lima"), and the fuller name is kept. A placeholder such as "Speaker 2", or
    the prompt's own example copied back, is no name at all and is ignored.
    """
    eligible = set(eligible_labels)
    candidates: dict[str, list[str]] = {}
    for mapping in mappings:
        for label, name in mapping.items():
            name = " ".join(name.split())
            if (
                label in eligible
                and not is_placeholder_speaker_name(name)
                and name.casefold() not in _NO_NAME
            ):
                candidates.setdefault(label, []).append(name)

    agreed: dict[str, str] = {}
    disputed: dict[str, tuple[str, ...]] = {}
    for label, names in candidates.items():
        fullest = max(names, key=lambda name: len(name.split()))
        if all(_name_words(name) <= _name_words(fullest) for name in names):
            agreed[label] = fullest
        else:
            distinct: dict[str, str] = {}
            for name in names:
                distinct.setdefault(name.casefold(), name)
            disputed[label] = tuple(distinct.values())
    return SpeakerNames(agreed=agreed, disputed=disputed)


# --- The run ---------------------------------------------------------------


class _PartsRun:
    """Drafts the parts of one meeting and combines the drafts."""

    def __init__(
        self, context: LongMeetingContext, complete: Complete, budget: PartsBudget
    ):
        self.context = context
        self.complete = complete
        self.budget = budget
        self.mappings: list[dict[str, str]] = []
        self.disputed: dict[str, tuple[str, ...]] = {}
        self.total = 0
        self.words = self._draft_words()
        # The final notes, within most of the room kept for their answer.
        self.final_words = self._words(budget.merge_answer_tokens * 3 // 5)

    def _ask(self, prompt: str, read: Callable[[str], T]) -> T:
        """The answer, read; asked once more if the first cannot be read."""
        try:
            return read(self.complete(prompt))
        except MeetingIntelligenceContractError as error:
            logger.warning(
                "Unusable answer for long-meeting notes, asking again: %s", error
            )
            return read(self.complete(prompt))

    def _words(self, tokens: int) -> int:
        """Words of Markdown, at about seven bytes each, that fit in ``tokens``."""
        words = int(tokens / self.budget.prose_tokens_per_byte / 7)
        return max(100, words // 50 * 50)

    def _draft_words(self) -> int:
        """About how many words a draft, or notes combined short of the end, may run to.

        Enough for DRAFTS_PER_MERGE of them to share a combining call, and for
        a draft to fit the room left for its answer, so every round of
        combining shrinks the notes it starts with.
        """
        budget = self.budget
        fixed = budget.prose_tokens(build_merge_prompt(self.context, [], 0, final=True))
        merge_room = budget.window - budget.merge_answer_tokens - fixed
        return self._words(
            min(merge_room // DRAFTS_PER_MERGE, budget.draft_answer_tokens * 3 // 4)
        )

    def _part_limit(self) -> int:
        probe = TranscriptPart(text="", start="00:00:00", end="00:00:00")
        fixed = self.budget.tokens(
            build_part_prompt(self.context, 999, 999, probe, self.words)
        )
        window = self.budget.window
        room = window - self.budget.draft_answer_tokens - fixed
        if room < window // 4:
            raise ContextWindowExceededError(
                "The meeting does not fit the context window, and the rest of "
                "the notes prompt (attached documents, notes structure and "
                f"meeting details) takes about {fixed:,} of its {window:,} "
                "tokens, which leaves too little room to write the notes in "
                "parts. Remove large attached documents, increase the context "
                "window, or use a provider with a larger context.",
                window=window,
            )
        return self.budget.bytes_for(room)

    def _draft(self, part: TranscriptPart, index: int) -> str:
        prompt = build_part_prompt(self.context, index, self.total, part, self.words)
        if self.context.request is None:
            return self._ask(prompt, _read_markdown)
        mapping, notes = self._ask(prompt, _read_draft)
        self.mappings.append(mapping)
        return notes

    def draft_all(self, transcript: str) -> list[_Notes]:
        queue = split_transcript(transcript, self._part_limit())
        self.total = len(queue)
        drafts: list[_Notes] = []
        while queue:
            part = queue.pop(0)
            index = len(drafts) + 1
            try:
                text = self._draft(part, index)
            except ContextWindowExceededError:
                # The estimate ran low, or the answer needed more room than it
                # was left: halve the part and draft the halves.
                if part.size < MIN_PART_BYTES:
                    raise
                # Two thirds as the limit leaves the halves room either side
                # of an equal share.
                halves = split_transcript(part.text, part.size * 2 // 3 + 1)
                queue[:0] = halves
                self.total += len(halves) - 1
                logger.info(
                    "Part %s did not fit the context window; drafting it as %s parts",
                    index,
                    len(halves),
                )
                continue
            drafts.append(_Notes(text, index, index, part.start, part.end))
            logger.info("Drafted notes for part %s of %s", index, self.total)
        return drafts

    def _merge_prompt(
        self, items: Sequence[_Notes], *, final: bool, words: Optional[int] = None
    ) -> str:
        words = words or (self.final_words if final else self.words)
        return build_merge_prompt(
            self.context,
            items,
            self.total,
            final=final,
            words=words,
            disputed=self.disputed if final else None,
        )

    def _fits(self, items: Sequence[_Notes], *, final: bool) -> bool:
        prompt = self._merge_prompt(items, final=final)
        answer = self.budget.merge_answer_tokens
        return self.budget.prose_tokens(prompt) + answer <= self.budget.window

    def _pack(self, items: Sequence[_Notes]) -> list[list[_Notes]]:
        """Consecutive groups, each as many drafts as one combining call can take."""
        groups: list[list[_Notes]] = [[]]
        for item in items:
            if groups[-1] and not self._fits([*groups[-1], item], final=False):
                groups.append([])
            groups[-1].append(item)
        return groups

    def _combine(
        self,
        items: Sequence[_Notes],
        *,
        final: bool,
        read: Callable[[str], T],
    ) -> T:
        """One combining call, asked once more for half the words if its answer ran out.

        A model can overrun the length it was given. Halving the items would not
        help then, since the prompt fitted, so the shorter answer is tried first.
        """
        words = self.final_words if final else self.words
        try:
            return self._ask(self._merge_prompt(items, final=final, words=words), read)
        except ContextWindowExceededError as error:
            if not error.answer_incomplete:
                raise
            logger.info(
                "The notes combined from %s ran out of window; asking for at most "
                "%s words",
                _describe(items, self.total),
                words // 2,
            )
            return self._ask(
                self._merge_prompt(items, final=final, words=words // 2), read
            )

    def _combine_group(self, group: list[_Notes]) -> list[_Notes]:
        if len(group) == 1:
            return group
        try:
            text = self._combine(group, final=False, read=_read_markdown)
        except ContextWindowExceededError:
            logger.info(
                "Combining the notes of %s did not fit the context window; "
                "combining it in halves",
                _describe(group, self.total),
            )
            middle = len(group) // 2
            return [
                *self._combine_group(group[:middle]),
                *self._combine_group(group[middle:]),
            ]
        logger.info("Combined the notes of %s", _describe(group, self.total))
        return [
            _Notes(text, group[0].first, group[-1].last, group[0].start, group[-1].end)
        ]

    def combine(self, drafts: list[_Notes], read: Callable[[str], T]) -> T:
        """The final combining call's answer, after any rounds of combining in groups."""
        items = drafts
        while True:
            groups: list[list[_Notes]]
            # One item has nothing left to split: Ollama judges whether it fits.
            if len(items) == 1 or self._fits(items, final=True):
                try:
                    return self._combine(items, final=True, read=read)
                except ContextWindowExceededError:
                    if len(items) < 2:
                        raise
                    logger.info(
                        "The final combining call did not fit the context "
                        "window; combining its %s notes in halves first",
                        len(items),
                    )
                    middle = len(items) // 2
                    groups = [items[:middle], items[middle:]]
            else:
                groups = self._pack(items)
                if all(len(group) == 1 for group in groups):
                    # By the price no two drafts share a call; Ollama is the
                    # judge, so try them in pairs.
                    groups = [items[i : i + 2] for i in range(0, len(items), 2)]
            combined = [
                notes for group in groups for notes in self._combine_group(group)
            ]
            if len(combined) >= len(items):
                raise ContextWindowExceededError(
                    "The notes written for the parts of this meeting are too long "
                    "to combine within the context window. Increase the context "
                    "window, or use a provider with a larger context.",
                    window=self.budget.window,
                )
            items = combined


def generate_notes_in_parts(
    transcript: str,
    context: LongMeetingContext,
    complete: Complete,
    budget: PartsBudget,
) -> str:
    """Notes for a Regenerate Notes transcript too long for one call.

    Returns the final call's notes, which the backend finalises as it does the
    answer of a single call.
    """
    run = _PartsRun(context, complete, budget)
    drafts = run.draft_all(transcript)
    return run.combine(drafts, _read_markdown)


def generate_meeting_intelligence_in_parts(
    request: AutomaticMeetingIntelligenceRequest,
    complete: Complete,
    budget: PartsBudget,
) -> AutomaticMeetingIntelligenceResult:
    """Speaker suggestions, title and notes for a meeting too long for one call."""
    run = _PartsRun(context_for_request(request), complete, budget)
    drafts = run.draft_all(request.resolved_transcript)
    names = reconcile_speaker_mappings(
        run.mappings, request.unresolved_speakers
    ).with_introductions(
        introduced_names(
            request.resolved_transcript,
            request.unresolved_speakers,
            request.meeting_context,
        )
    )
    run.disputed = names.disputed
    title, notes, chosen = run.combine(drafts, _title_and_notes_reader(request))
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping=names.settle(chosen),
        title=title,
        notes_markdown=notes,
        require_section_heading=not request.uses_custom_notes_sections,
    )
    result.validate_for_request(request)
    return finalise_automatic_meeting_intelligence_result(result)
