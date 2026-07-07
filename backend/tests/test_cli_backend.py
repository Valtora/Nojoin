"""Unit tests for the CLI OAuth backend adapter.

Verifies the build -> call -> parse wiring: each method builds the inherited
prompt, hands it to the manager, and runs the inherited parser on the result.
The manager is faked, so no Claude Agent SDK / io image is needed.
"""

from __future__ import annotations

import pytest

from backend.processing.cli_backend import (
    _CLI_MODELS,
    CliLLMBackend,
    CliOAuthUnavailableError,
)


class _FakeManager:
    def __init__(self, response="", chunks=None):
        self.response = response
        self.chunks = list(chunks or [])
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def run_single_turn(self, user_id, prompt, *, model=None, timeout=300):
        self.calls.append({"user_id": user_id, "prompt": prompt, "model": model})
        return self.response

    def stream_single_turn(self, user_id, prompt, *, model=None, timeout=300):
        self.stream_calls.append({"user_id": user_id, "prompt": prompt, "model": model})
        yield from self.chunks


def _backend(response="", chunks=None):
    backend = CliLLMBackend(model="claude-sonnet-5", user_id=7)
    backend._manager = _FakeManager(response=response, chunks=chunks)
    return backend


def test_generate_meeting_notes_builds_prompt_and_returns_parsed():
    backend = _backend(response="## Summary\n- Shipped the release")
    notes = backend.generate_meeting_notes(
        transcript="SPEAKER_00: we shipped the release",
        speaker_mapping={"SPEAKER_00": "Alex"},
    )
    assert "Shipped the release" in notes
    call = backend._manager.calls[0]
    assert call["user_id"] == 7
    assert call["model"] == "claude-sonnet-5"
    assert "shipped the release" in call["prompt"].lower()


def test_infer_meeting_title_parsed():
    backend = _backend(response="Weekly Release Sync")
    assert (
        backend.infer_meeting_title(transcript="some transcript")
        == "Weekly Release Sync"
    )


def test_infer_speaker_suggestions_parses_json():
    backend = _backend(response='{"suggestions": []}')
    result = backend.infer_speaker_suggestions(transcript="hello")
    assert result.mapping == {}


def test_ask_question_returns_raw_text_and_folds_history():
    backend = _backend(response="It was about the release.")
    answer = backend.ask_question_about_meeting(
        user_question="What was discussed?",
        meeting_notes="Notes",
        diarized_transcript="Alex: release",
        conversation_history=[
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "model", "parts": [{"text": "hello there"}]},
        ],
    )
    assert answer == "It was about the release."
    prompt = backend._manager.calls[0]["prompt"]
    assert "Prior conversation" in prompt
    assert "User: hi" in prompt
    assert "Assistant: hello there" in prompt
    assert "What was discussed?" in prompt


def test_ask_question_streaming_yields_chunks():
    backend = _backend(chunks=["Hel", "lo"])
    chunks = list(
        backend.ask_question_streaming(
            user_question="hi", meeting_notes="", diarized_transcript=""
        )
    )
    assert chunks == ["Hel", "lo"]


def test_meeting_edge_degrades():
    with pytest.raises(CliOAuthUnavailableError):
        _backend().generate_meeting_edge(request=None)


def test_list_models_is_static_curated_set():
    assert _backend().list_models() == list(_CLI_MODELS)


def test_validate_api_key_roundtrips():
    backend = _backend(response="OK")
    assert backend.validate_api_key() is True
    assert backend._manager.calls[0]["prompt"] == "Reply with exactly: OK"
