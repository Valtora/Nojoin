"""Live calls make one request, within their timeout (#994).

Meeting Edge and the live naming ask run on a copy of each backend made by
``for_live_calls``: no SDK retries, no waiting out a ``Retry-After`` inside the
call, OpenAI's second request only after a refusal, and Ollama's repair call
only inside the first call's deadline. Every other call keeps the backend's
own behaviour, and the tests marked "as before" pin that.

Each case goes through the backend's real method and the installed SDK, with
only the HTTP transport replaced. Nothing leaves the process.
"""

from __future__ import annotations

import importlib
import json
import time
from typing import Any

import pytest
import requests

import backend.worker.tasks as tasks_module
from backend.processing.llm_backends import ollama as ollama_module
from backend.processing.llm_backends.base import LLMBackend, live_call_backend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.tests.live_naming_fixtures import ONE
from backend.tests.test_live_speaker_naming_llm import (
    AI_SETTINGS,
    _refresh,
    _say,
    _seed_owner,
)
from backend.tests.test_live_speaker_naming_llm import llm as llm
from backend.tests.test_live_speaker_naming_llm import (
    session_maker as session_maker,
)
from backend.tests.test_meeting_edge_toggle import (
    _live_edge_session,
    _run_live_edge_refresh,
)
from backend.utils.meeting_edge import MeetingEdgeRequest

TIMEOUT = 90
MEETING_EDGE_REPLY = json.dumps(
    {
        "summary": "The team agreed the budget.",
        "rolling_summary": "The team went through the budget and agreed it.",
        "questions": ["Who owns the follow-up?"],
        "points": ["The budget is agreed."],
        "concepts": [],
    }
)
# Long enough that a call which waited it out would fail the time check.
RETRY_AFTER_SECONDS = "30"
# What a call that does not wait may take, with a wide margin for a slow runner.
NO_WAIT_SECONDS = 5.0


class Transport:
    """Answers every request with one outcome, and records what was sent.

    An outcome is ``timeout``, ``429`` (with ``Retry-After``), ``500``,
    ``quick-500`` (with ``retry-after-ms: 1``, so the SDKs retry at once),
    ``ok``, or ``400-then-ok``.
    """

    def __init__(self, http_lib: Any, outcome: str, success_body: Any) -> None:
        self.http_lib = http_lib
        self.outcome = outcome
        self.success_body = success_body
        self.timeouts: list[Any] = []

    @property
    def requests(self) -> int:
        return len(self.timeouts)

    def __call__(self, request: Any) -> Any:
        http_lib = self.http_lib
        self.timeouts.append(request.extensions.get("timeout"))
        error = {"error": {"code": 0, "message": "provider said no", "type": "error"}}
        if self.outcome == "timeout":
            raise http_lib.ReadTimeout("timed out", request=request)
        if self.outcome == "429":
            return http_lib.Response(
                429, headers={"retry-after": RETRY_AFTER_SECONDS}, json=error
            )
        if self.outcome == "500":
            return http_lib.Response(500, json=error)
        if self.outcome == "quick-500":
            return http_lib.Response(500, headers={"retry-after-ms": "1"}, json=error)
        if self.outcome == "400-then-ok" and self.requests == 1:
            return http_lib.Response(400, json=error)
        return http_lib.Response(200, json=self.success_body(MEETING_EDGE_REPLY))


def _http_lib_of(http_client: Any) -> Any:
    for cls in type(http_client).__mro__:
        package = (cls.__module__ or "").split(".")[0]
        if package in ("httpx", "httpx2"):
            return importlib.import_module(package)
    raise AssertionError("the SDK built neither an httpx nor an httpx2 client")


def _anthropic(outcome: str) -> tuple[Any, Transport]:
    import anthropic

    from backend.processing.llm_backends.anthropic import AnthropicLLMBackend

    http_lib = _http_lib_of(anthropic.Anthropic(api_key="not-used")._client)
    transport = Transport(
        http_lib,
        outcome,
        lambda text: {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )
    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-test"
    backend.client = anthropic.Anthropic(
        api_key="not-used",
        base_url="https://anthropic.example.test",
        http_client=http_lib.Client(transport=http_lib.MockTransport(transport)),
    )
    return backend, transport


def _openai(outcome: str) -> tuple[Any, Transport]:
    import openai

    from backend.processing.llm_backends.openai import OpenAILLMBackend

    http_lib = _http_lib_of(openai.OpenAI(api_key="sk-not-used")._client)
    transport = Transport(
        http_lib,
        outcome,
        lambda text: {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-test"
    backend.client = openai.OpenAI(
        api_key="sk-not-used",
        base_url="https://openai.example.test/v1",
        http_client=http_lib.Client(transport=http_lib.MockTransport(transport)),
    )
    return backend, transport


def _gemini(outcome: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Transport]:
    from google import genai

    from backend.processing.llm_backends.gemini import GeminiLLMBackend

    monkeypatch.delenv("GOOGLE_GENAI_API_VERSION", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_HTTP_CLIENT", raising=False)
    real_client = genai.Client
    http_lib = _http_lib_of(real_client(api_key="not-used")._api_client._httpx_client)
    transport = Transport(
        http_lib,
        outcome,
        lambda text: {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": "STOP",
                }
            ]
        },
    )
    client_args = {"transport": http_lib.MockTransport(transport)}

    def client_on_mock_transport(*args: Any, http_options: Any = None, **kwargs: Any):
        options = genai.types.HttpOptions.model_validate(http_options or {})
        options = options.model_copy(update={"client_args": client_args})
        return real_client(*args, http_options=options, **kwargs)

    monkeypatch.setattr(genai, "Client", client_on_mock_transport)
    return GeminiLLMBackend(api_key="not-used", model="gemini-test"), transport


class OllamaRequests:
    """Stands in for the ``requests`` module the Ollama backend calls."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.timeouts: list[Any] = []

    @property
    def requests(self) -> int:
        return len(self.timeouts)

    def post(self, url: str, json: Any = None, timeout: Any = None, **kwargs: Any):
        response = requests.Response()
        response.url = url
        if url.endswith("/api/show"):
            # The model's facts, read before the chat; not a chat request.
            response.status_code = 200
            response._content = b'{"model_info": {}, "capabilities": ["completion"]}'
            return response
        self.timeouts.append(timeout)
        if self.outcome == "timeout":
            raise requests.ReadTimeout("read timed out")
        if self.outcome in {"429", "500"}:
            response.status_code = int(self.outcome)
            response.headers["Retry-After"] = RETRY_AFTER_SECONDS
            response._content = b'{"error": "provider said no"}'
            return response
        broken = self.outcome == "broken-then-ok" and self.requests == 1
        response.status_code = 200
        response._content = _json_module.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": "not json" if broken else MEETING_EDGE_REPLY,
                },
                "done_reason": "stop",
            }
        ).encode()
        return response


_json_module = json


def _ollama(outcome: str) -> tuple[Any, OllamaRequests]:
    from backend.processing.llm_backends.ollama import OllamaLLMBackend

    backend = OllamaLLMBackend(
        api_url="http://10.0.0.5:11434",
        model="llama-test",
        context_window=8192,
        allow_private_api_url=True,
        think=False,
    )
    fake = OllamaRequests(outcome)
    backend.requests = fake
    return backend, fake


def _backend(provider: str, outcome: str, monkeypatch: pytest.MonkeyPatch):
    if provider == "anthropic":
        return _anthropic(outcome)
    if provider == "openai":
        return _openai(outcome)
    if provider == "gemini":
        return _gemini(outcome, monkeypatch)
    return _ollama(outcome)


def _meeting_edge(backend: Any) -> Any:
    return backend.generate_meeting_edge(
        MeetingEdgeRequest(recent_transcript="[00:01] SPEAKER_00: The budget."),
        timeout=TIMEOUT,
    )


def _speaker_suggestions(backend: Any) -> Any:
    return backend.infer_speaker_suggestions(
        "[00:01] SPEAKER_00: I'm Alex.",
        timeout=TIMEOUT,
        eligible_labels=["SPEAKER_00"],
    )


def _read_timeout(sent: Any) -> Any:
    """The read timeout a request was sent with, in seconds, or None."""
    return sent.get("read") if isinstance(sent, dict) else sent


PROVIDERS = ["anthropic", "openai", "gemini", "ollama"]
CALLS = {"meeting_edge": _meeting_edge, "speaker_suggestions": _speaker_suggestions}


@pytest.mark.parametrize("call", list(CALLS))
@pytest.mark.parametrize("outcome", ["timeout", "429", "500"])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_live_call_makes_one_request_and_does_not_wait(
    provider, outcome, call, monkeypatch
):
    backend, transport = _backend(provider, outcome, monkeypatch)
    live = backend.for_live_calls()

    started = time.monotonic()
    with pytest.raises(RuntimeError):
        CALLS[call](live)
    elapsed = time.monotonic() - started

    assert transport.requests == 1
    assert _read_timeout(transport.timeouts[0]) == TIMEOUT
    assert elapsed < NO_WAIT_SECONDS


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_making_a_live_copy_leaves_the_backend_as_it_was(provider, monkeypatch):
    backend, _ = _backend(provider, "ok", monkeypatch)
    client = backend.client

    live = backend.for_live_calls()

    assert live.client.max_retries == 0
    assert backend.client is client and client.max_retries == 2
    assert backend._live_calls is False and live._live_calls is True


def test_a_live_openai_call_still_retries_without_json_mode_after_a_refusal():
    backend, transport = _openai("400-then-ok")

    result = _meeting_edge(backend.for_live_calls())

    assert result.summary == "The team agreed the budget."
    assert transport.requests == 2


def test_a_live_ollama_repair_gets_what_is_left_of_the_deadline(monkeypatch):
    backend, fake = _ollama("broken-then-ok")
    clock = iter([100.0, 130.0])
    monkeypatch.setattr(ollama_module, "monotonic", lambda: next(clock))

    result = _meeting_edge(backend.for_live_calls())

    assert result.summary == "The team agreed the budget."
    assert fake.timeouts == [TIMEOUT, TIMEOUT - 30.0]


def test_a_live_ollama_call_with_no_time_left_sends_no_repair(monkeypatch):
    backend, fake = _ollama("broken-then-ok")
    clock = iter([100.0, 100.0 + TIMEOUT - 1])
    monkeypatch.setattr(ollama_module, "monotonic", lambda: next(clock))

    with pytest.raises(RuntimeError, match="Meeting Edge"):
        _meeting_edge(backend.for_live_calls())

    assert fake.requests == 1


# -- calls that are not live behave as before ------------------------------


@pytest.mark.parametrize(
    ("provider", "call", "requests_sent", "read_timeout"),
    [
        # The SDK's two retries, each request held to the call's timeout.
        ("anthropic", "meeting_edge", 3, TIMEOUT),
        # No timeout passed: the SDK's own 600 s, and its two retries.
        ("anthropic", "speaker_suggestions", 3, 600),
        # Three attempts in JSON mode, then three more without it.
        ("openai", "meeting_edge", 6, TIMEOUT),
        ("openai", "speaker_suggestions", 3, TIMEOUT),
        # google-genai makes one attempt; speaker suggestions set no timeout.
        ("gemini", "meeting_edge", 1, TIMEOUT),
        ("gemini", "speaker_suggestions", 1, None),
        ("ollama", "meeting_edge", 1, TIMEOUT),
        ("ollama", "speaker_suggestions", 1, TIMEOUT),
    ],
)
def test_a_call_that_is_not_live_behaves_as_before(
    provider, call, requests_sent, read_timeout, monkeypatch
):
    outcome = "500" if provider == "ollama" else "quick-500"
    backend, transport = _backend(provider, outcome, monkeypatch)

    with pytest.raises(RuntimeError):
        CALLS[call](backend)

    assert transport.requests == requests_sent
    assert {_read_timeout(sent) for sent in transport.timeouts} == {read_timeout}


def test_an_ollama_repair_outside_a_live_call_gets_a_full_timeout(monkeypatch):
    backend, fake = _ollama("broken-then-ok")
    clock = iter([100.0, 100.0 + TIMEOUT - 1])
    monkeypatch.setattr(ollama_module, "monotonic", lambda: next(clock))

    _meeting_edge(backend)

    assert fake.timeouts == [TIMEOUT, TIMEOUT]


# -- the chain and the two live callers -------------------------------------


def test_a_chains_live_copy_makes_each_provider_live():
    primary, _ = _anthropic("ok")
    secondary, _ = _ollama("ok")
    cli = LLMBackend()
    chain = SecondaryLLMBackend(primary, SecondaryLLMBackend(secondary, cli))

    live = chain.for_live_calls()

    assert live._primary._live_calls is True
    assert live._secondary._primary._live_calls is True
    # A CLI subscription's timeout already bounds its whole turn.
    assert live._secondary._secondary is cli
    assert chain._primary is primary and primary._live_calls is False


def test_only_a_backend_has_a_live_copy():
    stand_in = object()

    assert live_call_backend(stand_in) is stand_in


def test_a_meeting_edge_refresh_sends_each_provider_one_request(
    monkeypatch, fake_single_flight
):
    primary, primary_sent = _anthropic("quick-500")
    secondary, secondary_sent = _openai("quick-500")

    _run_live_edge_refresh(
        monkeypatch, _live_edge_session(), SecondaryLLMBackend(primary, secondary)
    )

    assert (primary_sent.requests, secondary_sent.requests) == (1, 1)


@pytest.mark.anyio
async def test_the_live_naming_ask_holds_each_provider_to_its_timeout(
    session_maker: Any, llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, primary_sent = _anthropic("timeout")
    secondary, secondary_sent = _gemini("timeout", monkeypatch)
    monkeypatch.setattr(
        tasks_module,
        "_llm_backend_from_config",
        lambda config: SecondaryLLMBackend(primary, secondary),
    )
    await _seed_owner(session_maker, AI_SETTINGS)
    await _say(session_maker, ONE, "I'm Alex.")

    await _refresh(session_maker, llm)

    assert primary_sent.requests == 1 and secondary_sent.requests == 1
    assert _read_timeout(primary_sent.timeouts[0]) == TIMEOUT
    assert _read_timeout(secondary_sent.timeouts[0]) == TIMEOUT
