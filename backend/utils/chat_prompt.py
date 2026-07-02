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

from typing import Any, Optional


def build_chat_context(meeting_notes: str, diarized_transcript: str) -> str:
    """Stable, cacheable chat context: instructions, notes, and full transcript."""
    return f"""
You are a helpful AI assistant. You have access to the following meeting notes, full diarized transcript, and potentially extracted context from related documents. Use this information to answer the user's question as accurately as possible. If the answer is not present, say so.

# CRITICAL INSTRUCTION
When referencing transcript content, always include the timestamp in [MM:SS] format (e.g., "At [12:30], Speaker A mentioned...").

# Meeting Notes:
{meeting_notes}

# Full Diarized Transcript:
{diarized_transcript}
"""


def build_chat_prompt(
    user_question: str, meeting_notes: str, diarized_transcript: str
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
