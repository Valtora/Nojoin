"""Unit tests for the CLI OAuth backend adapter.

Verifies the build -> call -> parse wiring: each method builds the inherited
prompt, hands it to the manager, and runs the inherited parser on the result.
The manager is faked, so no Claude Agent SDK / io image is needed.
"""

from __future__ import annotations

import pytest

from backend.processing.cli_backend import (
    _CLAUDE_CLI_MODELS,
    _CODEX_CLI_MODELS,
    CliLLMBackend,
)
from backend.utils.meeting_edge import MeetingEdgeRequest
from backend.utils.vision import VisionImage


class _FakeManager:
    def __init__(self, response="", chunks=None):
        self.response = response
        self.chunks = list(chunks or [])
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def run_single_turn(
        self, user_id, prompt, *, model=None, timeout=None, images=None
    ):
        self.calls.append(
            {"user_id": user_id, "prompt": prompt, "model": model, "timeout": timeout}
        )
        return self.response

    def stream_single_turn(self, user_id, prompt, *, model=None, timeout=None):
        self.stream_calls.append(
            {"user_id": user_id, "prompt": prompt, "model": model, "timeout": timeout}
        )
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


def test_generate_meeting_edge_builds_and_parses():
    backend = _backend(
        response=(
            '{"summary": "Launch timing is the active thread.", '
            '"rolling_summary": "The team is reviewing launch readiness.", '
            '"questions": ["Who owns final launch approval?"], '
            '"points": [], "concepts": []}'
        )
    )
    result = backend.generate_meeting_edge(
        MeetingEdgeRequest(
            recent_transcript="Alex: the Q3 launch is slipping because payments isn't done."
        ),
        timeout=90,
    )
    assert result.summary == "Launch timing is the active thread."
    assert result.questions == ("Who owns final launch approval?",)
    call = backend._manager.calls[0]
    # self.model is cli_live_model for the meeting_edge purpose (resolved upstream).
    assert call["model"] == "claude-sonnet-5"
    assert "Q3 launch" in call["prompt"]


def test_list_models_is_static_curated_set():
    assert _backend().list_models() == list(_CLAUDE_CLI_MODELS)


def test_list_models_is_provider_specific():
    assert CliLLMBackend(provider="codex").list_models() == list(_CODEX_CLI_MODELS)


def test_validate_api_key_roundtrips():
    backend = _backend(response="OK")
    assert backend.validate_api_key() is True
    assert backend._manager.calls[0]["prompt"] == "Reply with exactly: OK"


def _plumbing_only(backend):
    """Stub the inherited prompt builders and parsers: only the call matters here."""
    for name in (
        "build_speaker_suggestion_prompt",
        "build_notes_prompt",
        "build_automatic_meeting_intelligence_prompt",
        "build_meeting_edge_prompt",
        "build_meeting_analysis_prompt",
        "build_title_prompt",
    ):
        setattr(backend, name, lambda *args, **kwargs: "prompt")
    for name in (
        "parse_speaker_inference_result",
        "parse_automatic_meeting_intelligence_result",
        "parse_meeting_edge_result",
        "parse_meeting_analysis_result",
        "parse_title",
        "parse_notes",
        "finalise_meeting_notes",
    ):
        setattr(backend, name, lambda text, *args, **kwargs: text)
    return backend


# Each inference method, called with whatever limit the test gives (#980).
_CALLS = {
    "infer_speaker_suggestions": lambda b, **t: b.infer_speaker_suggestions(
        "transcript", **t
    ),
    "generate_meeting_notes": lambda b, **t: b.generate_meeting_notes(
        "transcript", {}, **t
    ),
    "generate_meeting_intelligence": lambda b, **t: b.generate_meeting_intelligence(
        object(), **t
    ),
    "generate_text": lambda b, **t: b.generate_text("prompt", **t),
    "generate_text_from_images": lambda b, **t: b.generate_text_from_images(
        "prompt", [VisionImage(data=b"png", media_type="image/png")], **t
    ),
    "generate_meeting_edge": lambda b, **t: b.generate_meeting_edge(object(), **t),
    "generate_meeting_analysis": lambda b, **t: b.generate_meeting_analysis(
        object(), **t
    ),
    "infer_meeting_title": lambda b, **t: b.infer_meeting_title("transcript", **t),
    "ask_question_about_meeting": lambda b, **t: b.ask_question_about_meeting(
        "question", "", "transcript", **t
    ),
    "ask_question_streaming": lambda b, **t: list(
        b.ask_question_streaming("question", "", "transcript", **t)
    ),
}


def _manager_timeouts(backend):
    manager = backend._manager
    return [call["timeout"] for call in manager.calls + manager.stream_calls]


@pytest.mark.parametrize("method", sorted(_CALLS))
@pytest.mark.parametrize("timeout", [90, 180])
def test_an_explicit_limit_reaches_the_manager(method, timeout):
    backend = _plumbing_only(_backend(response="answer", chunks=["answer"]))

    _CALLS[method](backend, timeout=timeout)

    assert _manager_timeouts(backend) == [timeout]


@pytest.mark.parametrize("method", sorted(_CALLS))
def test_an_omitted_limit_leaves_the_managers_default(method):
    # Not the hosted backends' per-request default of 60 s: on a CLI the limit
    # covers the whole answer, so a caller that gives none gets the manager's.
    backend = _plumbing_only(_backend(response="answer", chunks=["answer"]))

    _CALLS[method](backend)

    assert _manager_timeouts(backend) == [None]


def test_the_key_check_leaves_the_managers_default():
    backend = _backend(response="OK")

    backend.validate_api_key()

    assert _manager_timeouts(backend) == [None]
