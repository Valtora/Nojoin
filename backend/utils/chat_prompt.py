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

from backend.prompts import render_prompt

NO_TRANSCRIPT_SECTION = render_prompt("chat.no_transcript")

EXCERPTS_PREAMBLE = render_prompt("chat.excerpts_preamble")

NO_EXCERPTS_PREAMBLE = render_prompt("chat.no_excerpts_preamble")

# What a chat reply can do. Each reply is one request: nothing runs after it,
# so a model left to guess offers to monitor the meeting or send reminders.
# Only a request that passes the notes tool may say it can change the notes.
CAPABILITIES_WITH_NOTES_TOOL = render_prompt("chat.capabilities_with_notes_tool")

CAPABILITIES_WITHOUT_TOOLS = render_prompt("chat.capabilities_without_tools")


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
            render_prompt("chat.live_trimmed_scope")
            if diarized_transcript.trimmed
            else render_prompt("chat.live_scope")
        )
        return render_prompt(
            "chat.live_transcript",
            scope=scope,
            transcript=(
                diarized_transcript.text or render_prompt("chat.live_no_speech")
            ),
        )
    if isinstance(diarized_transcript, TranscriptExcerpts):
        if not diarized_transcript.text:
            return render_prompt(
                "chat.no_excerpt_transcript", preamble=NO_EXCERPTS_PREAMBLE
            )
        return render_prompt(
            "chat.excerpt_transcript",
            preamble=EXCERPTS_PREAMBLE,
            transcript=diarized_transcript.text,
        )
    # A missing transcript is stated as such, never passed off as transcript
    # text, so the model cannot quote a status message as something said.
    if not diarized_transcript:
        return NO_TRANSCRIPT_SECTION
    return render_prompt("chat.full_transcript", transcript=diarized_transcript)


def _sources(diarized_transcript: ChatTranscript) -> str:
    if isinstance(diarized_transcript, LiveChatTranscript):
        return render_prompt("chat.sources_live")
    if isinstance(diarized_transcript, TranscriptExcerpts):
        return render_prompt("chat.sources_excerpts")
    if diarized_transcript:
        return render_prompt("chat.sources_full")
    return render_prompt("chat.sources_notes")


def build_chat_context(
    meeting_notes: str,
    diarized_transcript: ChatTranscript,
    *,
    notes_tool: bool = False,
) -> str:
    """Stable, cacheable chat context: instructions, notes, and the transcript.

    ``notes_tool`` is True only when the request also sends the
    ``update_meeting_notes`` tool, so the prompt never claims an action the
    provider was not given.
    """
    sources = _sources(diarized_transcript)
    capabilities = (
        CAPABILITIES_WITH_NOTES_TOOL if notes_tool else CAPABILITIES_WITHOUT_TOOLS
    )
    return render_prompt(
        "chat.context",
        sources=sources,
        capabilities=capabilities,
        meeting_notes=meeting_notes,
        transcript_section=_transcript_section(diarized_transcript),
    )


def build_chat_prompt(
    user_question: str,
    meeting_notes: str,
    diarized_transcript: ChatTranscript,
    *,
    notes_tool: bool = False,
) -> str:
    """Single-string chat prompt (context + question) for providers without caching."""
    return build_chat_context(
        meeting_notes, diarized_transcript, notes_tool=notes_tool
    ) + render_prompt("chat.question", question=user_question)


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
    messages.append(
        {
            "role": "user",
            "content": render_prompt("chat.question", question=user_question),
        }
    )
    return messages
