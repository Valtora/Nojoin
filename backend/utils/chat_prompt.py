"""Shared chat-prompt assembly for the LLM backends.

Kept out of ``llm_services`` so the large stable context (meeting notes plus the
full diarized transcript) is defined once and laid out cache-first: providers
that support prompt caching reuse the leading context across turns, while the
volatile user question is always sent last.
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


def build_chat_messages(
    meeting_notes: str,
    diarized_transcript: str,
    user_question: str,
    conversation_history: Optional[list] = None,
    *,
    cache_context: bool = False,
) -> list[dict[str, Any]]:
    """Assemble chat messages cache-first: the stable context leads (optionally
    marked with Anthropic ``cache_control``), then prior turns, then the volatile
    question last. History role ``model`` is normalised to ``assistant``.
    """
    context = build_chat_context(meeting_notes, diarized_transcript)
    context_content: Any = (
        [{"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}]
        if cache_context
        else context
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": context_content}]
    for msg in conversation_history or []:
        if msg.get("role") and msg.get("parts"):
            role = "assistant" if msg["role"] == "model" else msg["role"]
            for part in msg["parts"]:
                messages.append({"role": role, "content": part["text"]})
    messages.append({"role": "user", "content": f"\nUser Question: {user_question}\n"})
    return messages
