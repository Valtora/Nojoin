"""In-flight Chat uses current context, shortening it only after a refusal."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from backend.models.recording import IN_FLIGHT_TRANSCRIPT_STATUSES, RecordingStatus
from backend.models.transcript import Transcript
from backend.processing.llm_backends.base import LLMBackend
from backend.utils.chat_prompt import LiveChatTranscript
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.llm_transcript import NOTES_LEGEND

ChatChunk = str | dict[str, Any]


def chat_transcript_in_progress(
    status: RecordingStatus, transcript: Transcript | None
) -> bool:
    """Whether Chat answers from the transcript so far rather than the whole one.

    Regenerating notes or speakers holds a recording in flight but leaves its
    transcript finished, so Chat reads that one whole, as for any finished
    recording.
    """
    return status in IN_FLIGHT_TRANSCRIPT_STATUSES and (
        transcript is None or transcript.transcript_status != "completed"
    )


def _recent_text(text: str, budget: int) -> str:
    """A UTF-8 bounded suffix, preserving the newest line's timestamp/name.

    The notes legend heads the text whenever it has notes, and explains note
    lines wherever they fall, so a shortened text keeps it.
    """
    lines = text.splitlines()
    opening = lines[:1] if lines[:1] == [NOTES_LEGEND] else []
    budget -= sum(len(line.encode("utf-8")) + 1 for line in opening)
    kept: list[str] = []
    size = 0
    for line in reversed(lines[len(opening) :]):
        cost = len(line.encode("utf-8")) + bool(kept)
        if size + cost > budget:
            clipped = _clip_line(line, budget - size - bool(kept))
            if clipped:
                kept.append(clipped)
            break
        kept.append(line)
        size += cost
    return "\n".join([*opening, *reversed(kept)]) if kept else ""


def _clip_line(line: str, budget: int) -> str:
    head, separator, body = line.partition(": ")
    prefix = f"{head}: [...] " if separator else "[...] "
    room = budget - len(prefix.encode("utf-8"))
    if room <= 0:
        return ""
    ending = (body if separator else line).encode("utf-8")[-room:]
    return prefix + ending.decode("utf-8", errors="ignore")


def stream_live_chat(
    ask: Callable[[LiveChatTranscript], Iterator[ChatChunk]], recording_id: int
) -> Iterator[ChatChunk]:
    """Try the full snapshot, then its newest suffix; never retry after output.

    Only transcript text shrinks: the question, notes, retrieval and history
    retain their existing provider handling. The answer ends with a footer,
    saved with it, saying it used the transcript so far and whether trimmed.
    """
    original = LLMBackend.get_mapped_transcript_for_llm(recording_id, live=True) or ""
    transcript = LiveChatTranscript(original)
    budget = len(original.encode("utf-8"))
    while True:
        started = False
        has_text = False
        try:
            for chunk in ask(transcript):
                if chunk == "":
                    continue
                started = True
                yield chunk
                has_text = has_text or isinstance(chunk, str)
            if has_text:
                yield _disclosure(transcript.trimmed)
            return
        except ContextWindowExceededError as overflow:
            if started or overflow.answer_incomplete or not transcript.text:
                raise
            budget //= 2
            recent = _recent_text(original, budget)
            if not recent:
                raise
            transcript = LiveChatTranscript(recent, trimmed=True)


def _disclosure(trimmed: bool) -> str:
    scope = (
        " Only the most recent part of the transcript was included to fit the model’s context."
        if trimmed
        else ""
    )
    return (
        "\n\n_Based on the transcript so far; wording and speakers may still change."
        + scope
        + "_"
    )
