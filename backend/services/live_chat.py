"""In-flight Chat uses current context, shortening it only after a refusal."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

from backend.processing.llm_backends.base import LLMBackend
from backend.utils.chat_prompt import LiveChatTranscript
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.llm_transcript import NOTES_LEGEND, RenderedTranscript

ChatChunk = str | dict[str, Any]


def _context_overflow(exc: Exception) -> bool:
    if isinstance(exc, ContextWindowExceededError):
        return not exc.answer_incomplete
    message = str(exc).lower()
    return bool(
        re.search(r"input token count(?:\s*\([^)]*\))?\s+exceeds", message)
    ) or any(
        marker in message
        for marker in (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "prompt is too long",
            "prompt too long",
            "input token count exceeds",
            "input is too long",
            "too many input tokens",
        )
    )


def _recent_text(text: str, budget: int) -> str:
    """A UTF-8 bounded suffix, preserving the newest line's timestamp/name."""
    lines: list[str] = []
    size = 0
    carries_note = False
    note_lines = (
        text.note_line_indices if isinstance(text, RenderedTranscript) else frozenset()
    )
    # The legend is prompt metadata, not an old meeting passage.
    candidates = list(enumerate(text.splitlines()))
    if note_lines and candidates and candidates[0][1] == NOTES_LEGEND:
        candidates = candidates[1:]
    for index, line in reversed(candidates):
        cost = len(line.encode("utf-8")) + bool(lines)
        has_note = carries_note or index in note_lines
        legend = len(NOTES_LEGEND.encode("utf-8")) + 1 if has_note else 0
        if size + cost + legend > budget:
            remaining = budget - size - legend - bool(lines)
            clipped = _clip_line(line, remaining)
            if clipped:
                lines.append(clipped)
                carries_note = has_note
            break
        lines.append(line)
        carries_note = has_note
        size += cost
    opening = [NOTES_LEGEND] if carries_note else []
    return "\n".join([*opening, *reversed(lines)])


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

    The context item is delayed until the successful attempt produces output,
    so both transports emit one accurate indication before the first token.
    Only transcript text shrinks: the question, notes, retrieval and history
    retain their existing provider handling.
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
                if not started:
                    yield {
                        "type": "context",
                        "provisional": True,
                        "transcript_trimmed": transcript.trimmed,
                    }
                    started = True
                yield chunk
                has_text = has_text or isinstance(chunk, str)
            if not started:
                yield {
                    "type": "context",
                    "provisional": True,
                    "transcript_trimmed": transcript.trimmed,
                }
            if has_text:
                yield _disclosure(transcript.trimmed)
            return
        except Exception as exc:
            if started or not transcript.text or not _context_overflow(exc):
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
