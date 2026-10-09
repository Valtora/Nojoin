"""Ollama must never answer from a prompt it silently cut short.

When a prompt is longer than ``num_ctx``, Ollama drops tokens from the front of
it (keeping only the first ``num_keep``) and answers anyway: notes generated
that way describe the end of the meeting and nothing says so. When the answer
fills the window, Ollama shifts the context and loses the start of the prompt
the same way. These tests pin the guard: refuse before sending a prompt that
cannot fit, and refuse a response whose token counts show the window filled.
"""

from __future__ import annotations

import pytest
from requests import HTTPError

from backend.processing.llm_backends.ollama import (
    ESTIMATED_BYTES_PER_TOKEN,
    OLLAMA_ANSWER_RESERVE_TOKENS,
    OllamaLLMBackend,
)

NOTES = "# Meeting Notes\n\n## Summary\nThe launch moves to Friday."


class _Response:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _FakeOllama:
    """Answers /api/show with a trained context length and /api/chat with notes."""

    def __init__(self, *, trained_context: int | None, chat_metadata: dict):
        self.trained_context = trained_context
        self.chat_metadata = chat_metadata
        self.chat_payloads: list[dict] = []
        self.show_calls = 0

    def post(self, url: str, json: dict, timeout: int, allow_redirects: bool):
        if url.endswith("/api/show"):
            self.show_calls += 1
            if self.trained_context is None:
                return _Response({"error": "not found"}, status_code=404)
            return _Response(
                {
                    "model_info": {
                        "general.architecture": "qwen3",
                        "qwen3.context_length": self.trained_context,
                    }
                }
            )
        self.chat_payloads.append(json)
        return _Response(
            {"message": {"content": NOTES}, "done": True, **self.chat_metadata}
        )


def _backend(fake: _FakeOllama, context_window: int) -> OllamaLLMBackend:
    backend = object.__new__(OllamaLLMBackend)
    backend.model = "qwen3:14b"
    backend.api_url = "http://ollama.local"
    backend.context_window = context_window
    backend.requests = fake
    return backend


def _transcript_of_tokens(tokens: int) -> str:
    return "x" * (tokens * ESTIMATED_BYTES_PER_TOKEN)


def _notes(backend: OllamaLLMBackend, transcript: str) -> str:
    return backend.generate_meeting_notes(transcript, {}, timeout=5)


def test_a_prompt_that_cannot_fit_is_refused_before_it_is_sent():
    fake = _FakeOllama(trained_context=131072, chat_metadata={"done_reason": "stop"})
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _notes(backend, _transcript_of_tokens(32768))

    assert fake.chat_payloads == []


def test_the_answer_reserve_counts_against_the_window():
    # A prompt that fits on its own but leaves no room for the notes would end
    # in a truncated answer or a context shift, so it is refused up front too.
    fake = _FakeOllama(trained_context=131072, chat_metadata={"done_reason": "stop"})
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _notes(backend, _transcript_of_tokens(32768 - OLLAMA_ANSWER_RESERVE_TOKENS))

    assert fake.chat_payloads == []


def test_a_prompt_ollama_truncated_is_refused_after_the_call():
    # The estimate can be low (digits and non-Latin text take more tokens per
    # byte); Ollama's own count is the authority. A truncated prompt comes back
    # exactly num_ctx tokens long with an ordinary "stop".
    fake = _FakeOllama(
        trained_context=131072,
        chat_metadata={
            "done_reason": "stop",
            "prompt_eval_count": 32768,
            "eval_count": 900,
        },
    )
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _notes(backend, "[00:00 - 00:04] Speaker 1: short")

    assert len(fake.chat_payloads) == 1


def test_an_answer_that_filled_the_window_is_refused():
    # Ollama shifts the context rather than stopping when the answer fills
    # the window, dropping the start of the prompt mid-answer.
    fake = _FakeOllama(
        trained_context=131072,
        chat_metadata={
            "done_reason": "stop",
            "prompt_eval_count": 30000,
            "eval_count": 2768,
        },
    )
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _notes(backend, "[00:00 - 00:04] Speaker 1: short")


def test_a_response_with_room_to_spare_is_kept():
    fake = _FakeOllama(
        trained_context=131072,
        chat_metadata={
            "done_reason": "stop",
            "prompt_eval_count": 30000,
            "eval_count": 2767,
        },
    )
    backend = _backend(fake, context_window=32768)

    assert "The launch moves to Friday." in _notes(backend, "short transcript")


def test_the_window_is_clamped_to_what_the_model_was_trained_on():
    # Ollama clamps num_ctx to the model's trained length at load, so a
    # truncation at 8192 must be recognised even with 32768 configured.
    fake = _FakeOllama(
        trained_context=8192,
        chat_metadata={
            "done_reason": "stop",
            "prompt_eval_count": 8192,
            "eval_count": 400,
        },
    )
    backend = _backend(fake, context_window=32768)

    with pytest.raises(RuntimeError, match="context window was exhausted"):
        _notes(backend, "short transcript")

    assert fake.chat_payloads[0]["options"]["num_ctx"] == 8192


def test_the_model_is_looked_up_once_per_backend():
    fake = _FakeOllama(trained_context=131072, chat_metadata={"done_reason": "stop"})
    backend = _backend(fake, context_window=32768)

    _notes(backend, "first")
    _notes(backend, "second")

    assert fake.show_calls == 1
    assert [p["options"]["num_ctx"] for p in fake.chat_payloads] == [32768, 32768]


def test_an_unknown_model_length_falls_back_to_the_configured_window():
    fake = _FakeOllama(trained_context=None, chat_metadata={"done_reason": "stop"})
    backend = _backend(fake, context_window=32768)

    _notes(backend, "short transcript")

    assert fake.chat_payloads[0]["options"]["num_ctx"] == 32768


def test_chat_history_ollama_may_drop_does_not_block_the_question():
    # Ollama drops the oldest turns of a chat history to fit num_ctx but always
    # keeps the last message, which carries the transcript and the question.
    # Only that message has to fit.
    fake = _FakeOllama(trained_context=131072, chat_metadata={"done_reason": "stop"})
    backend = _backend(fake, context_window=32768)
    long_turn = _transcript_of_tokens(20000)
    history = [
        {"role": "user", "parts": [{"text": long_turn}]},
        {"role": "model", "parts": [{"text": long_turn}]},
    ]

    answer = backend.ask_question_about_meeting(
        user_question="And then?",
        meeting_notes="notes",
        diarized_transcript="[00:00 - 00:04] Speaker 1: hi",
        conversation_history=history,
        timeout=5,
    )

    assert answer == NOTES
    assert len(fake.chat_payloads) == 1
