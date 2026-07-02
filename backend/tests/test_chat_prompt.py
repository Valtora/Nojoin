from __future__ import annotations

from backend.utils.chat_prompt import (
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


def test_build_chat_messages_leads_with_context_and_maps_history_roles() -> None:
    history = [
        {"role": "user", "parts": [{"text": "earlier Q"}]},
        {"role": "model", "parts": [{"text": "earlier A"}]},
    ]
    messages = build_chat_messages(NOTES, TRANSCRIPT, QUESTION, history)
    assert messages[0] == {
        "role": "user",
        "content": build_chat_context(NOTES, TRANSCRIPT),
    }
    assert messages[1] == {"role": "user", "content": "earlier Q"}
    # Gemini-style 'model' role is normalised to 'assistant'.
    assert messages[2] == {"role": "assistant", "content": "earlier A"}
    # The volatile question is always sent last.
    assert messages[-1] == {"role": "user", "content": f"\nUser Question: {QUESTION}\n"}


def test_build_chat_messages_cache_context_marks_prefix() -> None:
    messages = build_chat_messages(NOTES, TRANSCRIPT, QUESTION, cache_context=True)
    block = messages[0]["content"]
    assert isinstance(block, list)
    assert block[0]["type"] == "text"
    assert block[0]["text"] == build_chat_context(NOTES, TRANSCRIPT)
    assert block[0]["cache_control"] == {"type": "ephemeral"}
    # The cached prefix must never contain the per-turn question.
    assert QUESTION not in block[0]["text"]
    assert messages[-1] == {"role": "user", "content": f"\nUser Question: {QUESTION}\n"}


def test_build_chat_messages_without_history_is_context_then_question() -> None:
    messages = build_chat_messages(NOTES, TRANSCRIPT, QUESTION)
    assert len(messages) == 2
    assert messages[0]["content"] == build_chat_context(NOTES, TRANSCRIPT)
    assert messages[1] == {"role": "user", "content": f"\nUser Question: {QUESTION}\n"}
