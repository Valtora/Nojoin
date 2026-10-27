"""Shared chat-prompt assembly for the LLM backends.

Kept out of ``llm_services`` so the large stable context (meeting notes plus the
full diarized transcript) is defined once. It is laid out cache-first: the
context is sent via the provider's system field (Anthropic ``system=`` with a
cache breakpoint, or a leading OpenAI ``system`` message) so it forms a reusable
prefix across turns, while the volatile user question is always the last message.
Sending it out of band (rather than as a leading ``user`` message) keeps
user/assistant role alternation clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

NO_TRANSCRIPT_SECTION = """# Transcript:
This meeting has no transcript. If a question needs one, say that the transcript is not available.
"""

EXCERPTS_PREAMBLE = """This meeting is too long to include its full transcript, so only the passages most relevant to the question are below, in time order, with [...] where passages were left out. The meeting notes above cover the whole meeting. If neither the notes nor these passages answer the question, say so, and do not guess at what the rest of the transcript contains."""

NO_EXCERPTS_PREAMBLE = """This meeting is too long to include its full transcript, and no passages of it could be selected for this question. Answer from the meeting notes above, and say so when they do not cover the question."""


@dataclass(frozen=True)
class TranscriptExcerpts:
    """Passages of a transcript too long to send whole, chosen for one question.

    Meeting Chat sends these in place of the transcript after the provider
    refused the whole meeting. ``ranges`` are (start, end) times in seconds,
    most relevant first. A backend reads the passages from the transcript as
    it is at that moment and fills ``text`` before building the prompt (see
    ``LLMBackend._chat_transcript``).
    """

    ranges: tuple[tuple[float, float], ...]
    text: str = ""


@dataclass(frozen=True)
class LiveChatTranscript:
    """The current transcript snapshot used for one in-flight question."""

    text: str
    trimmed: bool = False


ChatTranscript = Union[str, TranscriptExcerpts, LiveChatTranscript, None]


def _transcript_section(diarized_transcript: ChatTranscript) -> str:
    if isinstance(diarized_transcript, LiveChatTranscript):
        scope = (
            "Only the most recent part of the transcript is included: older wording "
            "was trimmed to fit the model's context. Do not infer what the omitted "
            "part contains."
            if diarized_transcript.trimmed
            else "The transcript so far is included below."
        )
        return (
            "# Transcript So Far:\nThis recording is still in progress; wording and "
            "speakers may still change. " + scope + " Meeting notes may be incomplete "
            "or from an earlier processing pass; they do not establish what the "
            "whole meeting contains.\n\n"
            + (
                diarized_transcript.text
                or "No speech or timeline notes are available yet."
            )
            + "\n"
        )
    if isinstance(diarized_transcript, TranscriptExcerpts):
        if not diarized_transcript.text:
            return f"# Transcript:\n{NO_EXCERPTS_PREAMBLE}\n"
        return (
            f"# Transcript Excerpts:\n{EXCERPTS_PREAMBLE}\n\n"
            f"{diarized_transcript.text}\n"
        )
    # A missing transcript is stated as such, never passed off as transcript
    # text, so the model cannot quote a status message as something said.
    if not diarized_transcript:
        return NO_TRANSCRIPT_SECTION
    return f"# Full Diarized Transcript:\n{diarized_transcript}\n"


def _sources(diarized_transcript: ChatTranscript) -> str:
    if isinstance(diarized_transcript, LiveChatTranscript):
        return "meeting notes, transcript so far,"
    if isinstance(diarized_transcript, TranscriptExcerpts):
        return "meeting notes, excerpts from the diarized transcript,"
    if diarized_transcript:
        return "meeting notes, full diarized transcript,"
    return "meeting notes"


def build_chat_context(meeting_notes: str, diarized_transcript: ChatTranscript) -> str:
    """Stable, cacheable chat context: instructions, notes, and the transcript."""
    sources = _sources(diarized_transcript)
    return f"""
You are a helpful AI assistant. You have access to the following {sources} and potentially extracted context from related documents. Use this information to answer the user's question as accurately as possible. If the answer is not present, say so.

# CRITICAL INSTRUCTION
When referencing transcript content, always include the timestamp in [MM:SS] format (e.g., "At [12:30], Speaker A mentioned...").

# FORMATTING
When an answer compares several items across the same fields, present it as a Markdown table with a header row and a `| --- |` delimiter row beneath it. Keep tables to six columns or fewer, write a line break inside a cell as `<br>` rather than a real newline, and never merge cells.

# Meeting Notes:
{meeting_notes}

{_transcript_section(diarized_transcript)}"""


def build_chat_prompt(
    user_question: str, meeting_notes: str, diarized_transcript: ChatTranscript
) -> str:
    """Single-string chat prompt (context + question) for providers without caching."""
    return (
        build_chat_context(meeting_notes, diarized_transcript)
        + f"\nUser Question: {user_question}\n"
    )


def anthropic_cached_system(context: str) -> list[dict[str, Any]]:
    """Anthropic ``system`` blocks with a cache breakpoint on the stable context."""
    return [{"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}]


def build_chat_messages(
    user_question: str,
    conversation_history: Optional[list] = None,
) -> list[dict[str, Any]]:
    """Conversation turns for a chat request: prior history followed by the
    volatile question last. The Gemini-style ``model`` history role is normalised
    to ``assistant``. The stable meeting context is NOT included here — callers
    send it via the provider's system field (see the module docstring) so it
    caches without disturbing user/assistant role alternation.
    """
    messages: list[dict[str, Any]] = []
    for msg in conversation_history or []:
        if msg.get("role") and msg.get("parts"):
            role = "assistant" if msg["role"] == "model" else msg["role"]
            for part in msg["parts"]:
                messages.append({"role": role, "content": part["text"]})
    messages.append({"role": "user", "content": f"\nUser Question: {user_question}\n"})
    return messages
