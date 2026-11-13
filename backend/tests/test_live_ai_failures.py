"""The classifier the live calls' backoff and Test connection share (#994).

Each case is a real provider error: the backend's live Meeting Edge call runs
through the installed SDK with only the HTTP transport replaced, and the error
it raises is classified. Nothing leaves the process.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import requests

from backend.processing.cli.manager import (
    CliCredentialUnavailableError,
    CliOAuthUnavailableError,
    CliUsageLimitError,
)
from backend.processing.llm_backends.failures import Failure, classify_failure
from backend.processing.llm_backends.model_probe import classify_probe_error
from backend.utils.meeting_edge import MeetingEdgeContractError, MeetingEdgeRequest

REQUEST = MeetingEdgeRequest(recent_transcript="[00:01] SPEAKER_00: The budget.")


def _http_lib_of(http_client: Any) -> Any:
    for cls in type(http_client).__mro__:
        package = (cls.__module__ or "").split(".")[0]
        if package in ("httpx", "httpx2"):
            return importlib.import_module(package)
    raise AssertionError("the SDK built neither an httpx nor an httpx2 client")


def _responder(http_lib: Any, status: int | str, body: Any, headers: dict) -> Any:
    def respond(request: Any) -> Any:
        if status == "timeout":
            raise http_lib.ReadTimeout("timed out", request=request)
        if status == "refused":
            raise http_lib.ConnectError("connection refused", request=request)
        return http_lib.Response(int(status), headers=headers, json=body)

    return respond


def _anthropic(status: int | str, body: Any = None, headers: dict | None = None):
    import anthropic

    from backend.processing.llm_backends.anthropic import AnthropicLLMBackend

    http_lib = _http_lib_of(anthropic.Anthropic(api_key="not-used")._client)
    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-test"
    backend.client = anthropic.Anthropic(
        api_key="not-used",
        base_url="https://anthropic.example.test",
        http_client=http_lib.Client(
            transport=http_lib.MockTransport(
                _responder(http_lib, status, body or {}, headers or {})
            )
        ),
    )
    return backend.for_live_calls()


def _openai(status: int | str, body: Any = None, headers: dict | None = None):
    import openai

    from backend.processing.llm_backends.openai import OpenAILLMBackend

    http_lib = _http_lib_of(openai.OpenAI(api_key="sk-not-used")._client)
    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-test"
    backend.client = openai.OpenAI(
        api_key="sk-not-used",
        base_url="https://openai.example.test/v1",
        http_client=http_lib.Client(
            transport=http_lib.MockTransport(
                _responder(http_lib, status, body or {}, headers or {})
            )
        ),
    )
    return backend.for_live_calls()


def _gemini(monkeypatch, status: int | str, body: Any = None):
    from google import genai

    from backend.processing.llm_backends.gemini import GeminiLLMBackend

    monkeypatch.delenv("GOOGLE_GENAI_API_VERSION", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_HTTP_CLIENT", raising=False)
    real_client = genai.Client
    http_lib = _http_lib_of(real_client(api_key="not-used")._api_client._httpx_client)
    client_args = {
        "transport": http_lib.MockTransport(
            _responder(http_lib, status, body or {}, {})
        )
    }

    def client_on_mock_transport(*args: Any, http_options: Any = None, **kwargs: Any):
        options = genai.types.HttpOptions.model_validate(http_options or {})
        options = options.model_copy(update={"client_args": client_args})
        return real_client(*args, http_options=options, **kwargs)

    monkeypatch.setattr(genai, "Client", client_on_mock_transport)
    return GeminiLLMBackend(api_key="not-used", model="gemini-test").for_live_calls()


class _OllamaRequests:
    def __init__(self, status: int | str, headers: dict | None = None) -> None:
        self.status = status
        self.headers = headers or {}

    def post(self, url: str, json: Any = None, timeout: Any = None, **kwargs: Any):
        response = requests.Response()
        response.url = url
        if url.endswith("/api/show"):
            response.status_code = 200
            response._content = b'{"model_info": {}, "capabilities": ["completion"]}'
            return response
        if self.status == "timeout":
            raise requests.ReadTimeout("read timed out")
        if self.status == "refused":
            raise requests.ConnectionError("connection refused")
        response.status_code = int(self.status)
        response.headers.update(self.headers)
        response._content = b'{"error": "provider said no"}'
        return response


def _ollama(status: int | str, headers: dict | None = None):
    from backend.processing.llm_backends.ollama import OllamaLLMBackend

    backend = OllamaLLMBackend(
        api_url="http://10.0.0.5:11434",
        model="llama-test",
        context_window=8192,
        allow_private_api_url=True,
        think=False,
    )
    backend.requests = _OllamaRequests(status, headers)
    return backend.for_live_calls()


def _failure_of(backend: Any) -> Failure:
    with pytest.raises(Exception) as raised:
        backend.generate_meeting_edge(REQUEST, timeout=90)
    return classify_failure(raised.value)


def _error(kind: str, message: str = "no") -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}


def _gemini_error(status: int, state: str, details: list | None = None) -> dict:
    return {
        "error": {
            "code": status,
            "message": "the provider said no",
            "status": state,
            "details": details or [],
        }
    }


ANTHROPIC = [
    (401, _error("authentication_error"), {}, Failure("auth")),
    (403, _error("permission_error"), {}, Failure("auth")),
    (404, _error("not_found_error"), {}, Failure("unknown_model")),
    (400, _error("billing_error"), {}, Failure("billing")),
    (402, _error("billing_error"), {}, Failure("billing")),
    # Any other refusal is one request's: an over-long prompt, a filter.
    (400, _error("invalid_request_error"), {}, Failure("refused")),
    (429, _error("rate_limit_error"), {"retry-after": "17"}, Failure("rate_limit", 17)),
    (500, _error("api_error"), {}, Failure("unavailable")),
    (529, _error("overloaded_error"), {}, Failure("unavailable")),
    ("timeout", None, {}, Failure("timeout")),
    ("refused", None, {}, Failure("unreachable")),
]


@pytest.mark.parametrize(("status", "body", "headers", "expected"), ANTHROPIC)
def test_anthropic_failures_are_classified(status, body, headers, expected):
    assert _failure_of(_anthropic(status, body, headers)) == expected


def _openai_error(code: str, kind: str) -> dict:
    return {"error": {"message": "no", "type": kind, "code": code, "param": None}}


OPENAI = [
    (
        401,
        _openai_error("invalid_api_key", "invalid_request_error"),
        {},
        Failure("auth"),
    ),
    (
        404,
        _openai_error("model_not_found", "invalid_request_error"),
        {},
        Failure("unknown_model"),
    ),
    (
        429,
        _openai_error("insufficient_quota", "insufficient_quota"),
        {},
        Failure("billing"),
    ),
    (
        429,
        _openai_error("rate_limit_exceeded", "requests"),
        {"retry-after": "20"},
        Failure("rate_limit", 20),
    ),
    (503, _openai_error("server_error", "server_error"), {}, Failure("unavailable")),
    ("timeout", None, {}, Failure("timeout")),
]


@pytest.mark.parametrize(("status", "body", "headers", "expected"), OPENAI)
def test_openai_failures_are_classified(status, body, headers, expected):
    assert _failure_of(_openai(status, body, headers)) == expected


KEY_INVALID = [
    {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "API_KEY_INVALID",
        "domain": "googleapis.com",
    }
]
RETRY_INFO = [
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41s"}
]
GEMINI = [
    (400, _gemini_error(400, "INVALID_ARGUMENT", KEY_INVALID), Failure("auth")),
    (400, _gemini_error(400, "INVALID_ARGUMENT"), Failure("refused")),
    (403, _gemini_error(403, "PERMISSION_DENIED"), Failure("auth")),
    (404, _gemini_error(404, "NOT_FOUND"), Failure("unknown_model")),
    (
        429,
        _gemini_error(429, "RESOURCE_EXHAUSTED", RETRY_INFO),
        Failure("rate_limit", 41),
    ),
    (500, _gemini_error(500, "INTERNAL"), Failure("unavailable")),
    ("timeout", None, Failure("timeout")),
]


@pytest.mark.parametrize(("status", "body", "expected"), GEMINI)
def test_gemini_failures_are_classified(status, body, expected, monkeypatch):
    assert _failure_of(_gemini(monkeypatch, status, body)) == expected


OLLAMA = [
    (401, {}, Failure("auth")),
    (404, {}, Failure("unknown_model")),
    (429, {"Retry-After": "60"}, Failure("rate_limit", 60)),
    (500, {}, Failure("unavailable")),
    ("timeout", {}, Failure("timeout")),
    ("refused", {}, Failure("unreachable")),
]


@pytest.mark.parametrize(("status", "headers", "expected"), OLLAMA)
def test_ollama_failures_are_classified(status, headers, expected):
    assert _failure_of(_ollama(status, headers)) == expected


def test_a_subscription_limit_waits_for_its_reset():
    resets_at = datetime.now(timezone.utc) + timedelta(seconds=120)

    failure = classify_failure(CliUsageLimitError("limited", resets_at=resets_at))

    assert failure.kind == "rate_limit"
    assert 110 < (failure.retry_after_s or 0) <= 120


def test_a_subscription_without_a_usable_credential_cannot_clear():
    failure = classify_failure(CliCredentialUnavailableError("reconnect"))

    assert failure == Failure("auth") and not failure.clears_by_itself


def test_a_subscription_timeout_is_transient():
    try:
        try:
            raise TimeoutError
        except TimeoutError as inner:
            raise CliOAuthUnavailableError(
                "CLI inference timed out after 90s."
            ) from inner
    except CliOAuthUnavailableError as exc:
        assert classify_failure(exc) == Failure("timeout")


def test_an_unreadable_answer_is_a_bad_response():
    try:
        try:
            raise MeetingEdgeContractError("summary must be a string")
        except MeetingEdgeContractError as inner:
            raise RuntimeError("Gemini API error (Meeting Edge)") from inner
    except RuntimeError as exc:
        assert classify_failure(exc) == Failure("bad_response")


@pytest.mark.parametrize(
    ("failure", "clears"),
    [
        (Failure("auth"), False),
        (Failure("unknown_model"), False),
        (Failure("billing"), False),
        (Failure("rate_limit"), True),
        (Failure("refused"), True),
        (Failure("timeout"), True),
        (Failure("unavailable"), True),
    ],
)
def test_only_identified_causes_cannot_clear(failure, clears):
    assert failure.clears_by_itself is clears


def test_test_connection_reads_a_gemini_bad_key_as_a_refused_credential(monkeypatch):
    backend = _gemini(
        monkeypatch, 400, _gemini_error(400, "INVALID_ARGUMENT", KEY_INVALID)
    )
    with pytest.raises(Exception) as raised:
        backend.generate_meeting_edge(REQUEST, timeout=90)

    assert classify_probe_error(raised.value) == "auth"
