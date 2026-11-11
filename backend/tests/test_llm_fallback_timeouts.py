"""The fallback chain hands each backend the caller's timeout, or none (#980).

A caller that gives no timeout must reach each backend as "not given", so a
hosted provider applies its own per-request default and a CLI subscription the
manager's whole-turn default. SecondaryLLMBackend used to send its own 60, 120
or 300 s instead, which a CLI turn could not tell from a limit a caller chose.
"""

from __future__ import annotations

import inspect

import pytest

from backend.processing.cli.manager import CliOAuthUnavailableError
from backend.processing.cli_backend import CliLLMBackend
from backend.processing.llm_backends.anthropic import AnthropicLLMBackend
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.processing.llm_backends.gemini import GeminiLLMBackend
from backend.processing.llm_backends.ollama import OllamaLLMBackend
from backend.processing.llm_backends.openai import OpenAILLMBackend
from backend.utils.vision import VisionImage

# What SecondaryLLMBackend sent when a caller gave no timeout. Leaving the
# timeout out instead changes nothing for a hosted backend only while its own
# defaults stay these.
_WRAPPER_SENT = {
    "infer_speaker_suggestions": 60,
    "generate_meeting_intelligence": 60,
    "generate_text": 60,
    "generate_text_from_images": 120,
    "generate_meeting_edge": 60,
    "generate_meeting_analysis": 300,
    "generate_meeting_notes": 60,
    "infer_meeting_title": 60,
    "ask_question_about_meeting": 60,
    "ask_question_streaming": 60,
}


def _timeout_default(method) -> object:
    return inspect.signature(method).parameters["timeout"].default


@pytest.mark.parametrize(
    "backend_cls",
    [GeminiLLMBackend, OpenAILLMBackend, AnthropicLLMBackend, OllamaLLMBackend],
)
def test_hosted_defaults_match_what_the_wrapper_used_to_send(backend_cls):
    defaults = {
        name: _timeout_default(getattr(backend_cls, name)) for name in _WRAPPER_SENT
    }

    assert defaults == _WRAPPER_SENT


class _Hosted(LLMBackend):
    """A hosted backend's defaults, recording the limit each call ran with."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.seen: list = []

    def _answer(self, timeout):
        self.seen.append(timeout)
        if self.fail:
            raise RuntimeError("hosted provider unavailable")
        return "hosted"


def _hosted_method(name: str, default: int):
    if name == "ask_question_streaming":

        def stream(self, *args, timeout=default, **kwargs):
            yield self._answer(timeout)

        return stream

    def call(self, *args, timeout=default, **kwargs):
        return self._answer(timeout)

    return call


for _name, _default in _WRAPPER_SENT.items():
    setattr(_Hosted, _name, _hosted_method(_name, _default))


def _failing_cli(seen: list) -> CliLLMBackend:
    """A real CLI backend and manager whose subscription is unavailable.

    The turn records the limit the manager resolved, so "not given" reads as
    the manager's 300 s, then fails over like any CLI error.
    """
    backend = CliLLMBackend(model="claude-sonnet-5", user_id=7)
    manager = backend._manager
    manager._resolve_access_token = lambda user_id: "token"

    async def _turn(  # noqa: PLR0913 - the driver's signature
        user_id, prompt, access_token, model, timeout, images=None
    ):
        seen.append(timeout)
        raise CliOAuthUnavailableError("subscription unavailable")

    async def _stream(  # noqa: PLR0913 - the driver's signature
        user_id, prompt, access_token, model, timeout, usage_sink
    ):
        seen.append(timeout)
        raise CliOAuthUnavailableError("subscription unavailable")
        yield  # an async generator, as the real driver is

    manager._arun_single_turn = _turn
    manager._astream_for = _stream
    for name in (
        "build_speaker_suggestion_prompt",
        "build_notes_prompt",
        "build_automatic_meeting_intelligence_prompt",
        "build_meeting_edge_prompt",
        "build_meeting_analysis_prompt",
        "build_title_prompt",
    ):
        setattr(backend, name, lambda *args, **kwargs: "prompt")
    return backend


_CALLS = {
    "infer_speaker_suggestions": lambda b, **t: b.infer_speaker_suggestions(
        "transcript", **t
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
    "generate_meeting_notes": lambda b, **t: b.generate_meeting_notes(
        "transcript", {}, **t
    ),
    "infer_meeting_title": lambda b, **t: b.infer_meeting_title("transcript", **t),
    "ask_question_about_meeting": lambda b, **t: b.ask_question_about_meeting(
        "question", "", "transcript", **t
    ),
    "ask_question_streaming": lambda b, **t: "".join(
        b.ask_question_streaming("question", "", "transcript", **t)
    ),
}


def _chain():
    """A subscription, then the server's provider, then its fallback."""
    cli_seen: list = []
    server_primary = _Hosted(fail=True)
    server_fallback = _Hosted()
    chain = SecondaryLLMBackend(
        primary=_failing_cli(cli_seen),
        secondary=SecondaryLLMBackend(
            primary=server_primary, secondary=server_fallback
        ),
    )
    return chain, cli_seen, server_primary, server_fallback


@pytest.mark.parametrize("method", sorted(_CALLS))
def test_an_omitted_timeout_reaches_each_backend_as_not_given(method):
    chain, cli_seen, server_primary, server_fallback = _chain()

    assert _CALLS[method](chain) == "hosted"

    assert cli_seen == [300]
    assert server_primary.seen == [_WRAPPER_SENT[method]]
    assert server_fallback.seen == [_WRAPPER_SENT[method]]


@pytest.mark.parametrize("method", sorted(_CALLS))
def test_an_explicit_timeout_reaches_each_backend_unchanged(method):
    chain, cli_seen, server_primary, server_fallback = _chain()

    assert _CALLS[method](chain, timeout=45) == "hosted"

    assert cli_seen == [45]
    assert server_primary.seen == [45]
    assert server_fallback.seen == [45]
