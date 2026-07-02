from __future__ import annotations

from backend.utils.chat_prompt import (
    anthropic_cached_system,
    build_chat_context,
    build_chat_messages,
    build_chat_prompt,
)

NOTES = "# Notes\n- decided X"
TRANSCRIPT = "[00:01] Speaker A: hello\n[00:05] Speaker B: hi"
QUESTION = "What did Speaker A decide?"


def test_build_chat_prompt_is_context_plus_question_suffix() -> None:
    prompt = build_chat_prompt(QUESTION, NOTES, TRANSCRIPT)
    assert (
        prompt
        == build_chat_context(NOTES, TRANSCRIPT) + f"\nUser Question: {QUESTION}\n"
    )
    context = build_chat_context(NOTES, TRANSCRIPT)
    assert NOTES in context and TRANSCRIPT in context
    assert QUESTION not in context


def test_build_chat_messages_maps_history_roles_and_puts_question_last() -> None:
    history = [
        {"role": "user", "parts": [{"text": "earlier Q"}]},
        {"role": "model", "parts": [{"text": "earlier A"}]},
    ]
    messages = build_chat_messages(QUESTION, history)
    assert messages[0] == {"role": "user", "content": "earlier Q"}
    # Gemini-style 'model' role is normalised to 'assistant'.
    assert messages[1] == {"role": "assistant", "content": "earlier A"}
    # The volatile question is always sent last...
    assert messages[-1] == {"role": "user", "content": f"\nUser Question: {QUESTION}\n"}
    # ...and the stable context is NOT in the messages array (it goes to system).
    assert all(NOTES not in message["content"] for message in messages)


def test_build_chat_messages_without_history_is_just_the_question() -> None:
    messages = build_chat_messages(QUESTION)
    assert messages == [{"role": "user", "content": f"\nUser Question: {QUESTION}\n"}]


def test_anthropic_cached_system_marks_the_stable_prefix() -> None:
    context = build_chat_context(NOTES, TRANSCRIPT)
    system = anthropic_cached_system(context)
    assert system == [
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}
    ]
    # The question is never part of the cached system prefix.
    assert QUESTION not in system[0]["text"]
