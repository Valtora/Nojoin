"""Test connection runs each selected model (#991).

The provider cases go through each backend's real ``probe_model`` and the
installed SDK, with only the HTTP transport replaced, so a status code is
classified as the SDK actually raises it. Nothing leaves the process.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
import requests
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_db
from backend.api.v1.endpoints import setup
from backend.main import create_app
from backend.processing.llm_backends import model_probe
from backend.processing.llm_backends.model_probe import (
    ProbeReplyError,
    check_probe_reply,
    classify_probe_error,
    plan_probes,
    probe_one,
    safe_provider_detail,
)

GOOD_REPLY = '{"ok": true}'
PROSE_REPLY = "Sure! Here is the object you asked for."

# -- the shared module ------------------------------------------------------


@pytest.mark.parametrize("reply", [GOOD_REPLY, '```json\n{"ok": true}\n```'])
def test_a_reply_with_the_requested_object_passes(reply) -> None:
    check_probe_reply(reply)


@pytest.mark.parametrize("reply", ["", PROSE_REPLY, "[1]", '{"ok": false}'])
def test_any_other_reply_is_a_bad_response(reply) -> None:
    with pytest.raises(ProbeReplyError):
        check_probe_reply(reply)


def test_planning_probes_each_model_once_and_at_most_three() -> None:
    planned = plan_probes(
        [
            ("alpha", "main"),
            ("alpha", "meeting_edge"),
            ("", "main"),
            ("beta", "meeting_edge"),
            ("gamma", "main"),
            ("delta", "main"),
        ]
    )

    assert planned == [("alpha", "main"), ("beta", "meeting_edge"), ("gamma", "main")]


def test_provider_detail_carries_no_key_and_no_url() -> None:
    exc = RuntimeError(
        "401 for https://user:hunter2@example.test/v1?key=AIzaSyA1234567890abcdefghijk "
        "with sk-proj-abcdefghijklmnop and token=abc123 and the-real-key"
    )

    detail = safe_provider_detail(exc, secrets=["the-real-key"])

    for leaked in (
        "hunter2",
        "example.test",
        "AIzaSy",
        "sk-proj",
        "abc123",
        "the-real-key",
    ):
        assert leaked not in detail
    assert detail.startswith("401 for [url]")


# Built from pieces at runtime, so no credential-shaped literal sits in the repo.
FAKE = "-".join(["not", "a", "real", "credential", "42"])

# Each shape the pattern backstop covers: the provider text, then the detail.
CREDENTIAL_SHAPES = [
    pytest.param(f'{{"api_key": "{FAKE}"}}', '{"api_key": "[redacted]"}', id="json"),
    pytest.param(f"{{'api_key': '{FAKE}'}}", "{'api_key': '[redacted]'}", id="dict"),
    pytest.param(f'"apikey": "{FAKE}"', '"apikey": "[redacted]"', id="apikey"),
    pytest.param(f'"API-Key": "{FAKE}"', '"API-Key": "[redacted]"', id="api-key"),
    pytest.param(f'"key": "{FAKE}"', '"key": "[redacted]"', id="key"),
    pytest.param(f"'token': '{FAKE}'", "'token': '[redacted]'", id="token"),
    pytest.param(
        f'"access_token": "{FAKE}"', '"access_token": "[redacted]"', id="access_token"
    ),
    pytest.param(f'"secret": "{FAKE}"', '"secret": "[redacted]"', id="secret"),
    pytest.param(
        f'"client_secret": "{FAKE}"',
        '"client_secret": "[redacted]"',
        id="client_secret",
    ),
    pytest.param(f'"password": "{FAKE}"', '"password": "[redacted]"', id="password"),
    pytest.param(
        f'"Authorization": "Bearer {FAKE}"',
        '"Authorization": "[redacted]"',
        id="quoted authorization",
    ),
    pytest.param(
        f"Authorization: Bearer {FAKE}",
        "Authorization: Bearer [redacted]",
        id="authorization bearer",
    ),
    pytest.param(
        f"authorization: basic {FAKE}",
        "authorization: basic [redacted]",
        id="authorization basic",
    ),
    pytest.param(
        f"sent Bearer {FAKE} upstream", "sent Bearer [redacted] upstream", id="bearer"
    ),
    pytest.param(f"sent Basic {FAKE}", "sent Basic [redacted]", id="basic"),
    pytest.param(f"x-api-key: {FAKE}", "x-api-key: [redacted]", id="x-api-key"),
    pytest.param(
        f"X-Goog-Api-Key: {FAKE}", "X-Goog-Api-Key: [redacted]", id="x-goog-api-key"
    ),
    pytest.param(
        f"headers {{'x-api-key': '{FAKE}'}}",
        "headers {'x-api-key': '[redacted]'}",
        id="quoted header",
    ),
    pytest.param(f"api_key={FAKE}&alt=json", "api_key=[redacted]", id="query"),
    pytest.param(
        f"Invalid token: {FAKE}", "Invalid token: [redacted]", id="prose name"
    ),
    pytest.param(f'"api_key": "{FAKE}', '"api_key": "[redacted]"', id="unterminated"),
    pytest.param(f"accessToken={FAKE}", "accessToken=[redacted]", id="camelCase"),
    pytest.param(f"authToken: {FAKE}", "authToken: [redacted]", id="authToken"),
    pytest.param(f"apiToken: {FAKE}", "apiToken: [redacted]", id="apiToken"),
    pytest.param(f"secretkey={FAKE}", "secretkey=[redacted]", id="secretkey"),
    pytest.param(f"privateKey={FAKE}", "privateKey=[redacted]", id="privateKey"),
    pytest.param(
        f'"accessToken": "{FAKE}"', '"accessToken": "[redacted]"', id="quoted camelCase"
    ),
    pytest.param(
        f'"clientSecret": "{FAKE}"', '"clientSecret": "[redacted]"', id="clientSecret"
    ),
    pytest.param(
        f'"refreshToken":"{FAKE}"', '"refreshToken":"[redacted]"', id="refreshToken"
    ),
    pytest.param(
        f"password=ab,cd;ef&gh){FAKE}", "password=[redacted]", id="punctuated value"
    ),
    pytest.param(
        f'"x-api-key":["{FAKE}"]', '"x-api-key":["[redacted]"]', id="header list"
    ),
    pytest.param(
        f'headers={{"authorization": ["Bearer {FAKE}"]}}',
        'headers={"authorization": ["[redacted]"]}',
        id="authorization list",
    ),
    pytest.param(f"key=[{FAKE}]", "key=[[redacted]]", id="bracketed"),
    pytest.param(f"token: ({FAKE})", "token: ([redacted])", id="parenthesised"),
    pytest.param(
        f'"key": "ab\\"{FAKE}"', '"key": "[redacted]"', id="escaped double quote"
    ),
    pytest.param(
        f"'key': 'ab\\'{FAKE}'", "'key': '[redacted]'", id="escaped single quote"
    ),
    pytest.param(f"key%3D{FAKE}", "key%3D[redacted]", id="percent-encoded"),
    pytest.param(f'api_key"={FAKE}', 'api_key"=[redacted]', id="closing quote only"),
]


@pytest.mark.parametrize(("raw", "expected"), CREDENTIAL_SHAPES)
def test_provider_detail_redacts_credential_shaped_values(raw, expected) -> None:
    detail = safe_provider_detail(RuntimeError(raw))

    assert detail == expected
    assert FAKE not in detail


# Reasons an administrator needs, which must come through word for word.
PROVIDER_REASONS = [
    "model 'gemma4:12b' not found, try pulling it first",
    "The model gemini-flash-lite-latest does not exist",
    "Incorrect API key provided",
    "API key not valid. Please pass a valid API key.",
    "{'type': 'authentication_error', 'message': 'invalid x-api-key'}",
    "invalid x-api-key",
    "Rate limit reached for requests",
    "maximum context length is 8192 tokens",
    "max_tokens: 4096 > 2048, which is the maximum allowed number of output tokens",
    "Invalid token: the token has expired",
    "Missing bearer or basic authentication in header",
    "provide your API key in an Authorization header using Bearer auth",
    "{'code': 'invalid_api_key', 'param': None}",
    "model 'multitask-instruct' not found",
    "hotkey: press",
    "the key: value pairs were malformed",
    "unsupported parameter: token budget",
    "This model's maximum context length is 8192 tokens. However, your messages "
    "resulted in 9000 tokens.",
    "Error code: 404 - {'error': {'message': \"model 'x' not found\", "
    "'type': 'api_error'}}",
    "{'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API "
    "key.', 'status': 'INVALID_ARGUMENT', 'details': [{'reason': 'API_KEY_INVALID', "
    "'domain': 'googleapis.com'}]}}",
]


@pytest.mark.parametrize("reason", PROVIDER_REASONS)
def test_provider_detail_keeps_an_ordinary_reason_word_for_word(reason) -> None:
    limit = model_probe._DETAIL_MAX_CHARS
    shown = reason if len(reason) <= limit else reason[: limit - 3].rstrip() + "..."

    assert safe_provider_detail(RuntimeError(reason)) == shown
    # Past the length cap too, the patterns leave the reason alone.
    assert model_probe._redact_key_like(reason) == reason


def test_a_failed_probe_logs_only_the_redacted_detail(monkeypatch) -> None:
    class _Refused:
        def probe_model(self, prompt, *, timeout, max_output_tokens):
            raise RuntimeError(f"401 {{'x-api-key': '{FAKE}'}} for the-stored-key")

    logged: list[str] = []
    monkeypatch.setattr(
        model_probe.logger, "warning", lambda msg, *args: logged.append(msg % args)
    )

    result = probe_one(
        lambda model: _Refused(),
        "anthropic",
        "claude-test",
        "main",
        include_detail=True,
        secrets=["the-stored-key"],
    )

    [line] = logged
    assert line.endswith("error=401 {'x-api-key': '[redacted]'} for [redacted]")
    assert result.message.endswith("(401 {'x-api-key': '[redacted]'} for [redacted])")


def test_a_timeout_anywhere_in_the_chain_is_a_timeout() -> None:
    try:
        try:
            raise requests.ReadTimeout("read timed out")
        except requests.ReadTimeout as inner:
            raise RuntimeError("Ollama API error (probe)") from inner
    except RuntimeError as outer:
        assert classify_probe_error(outer) == "timeout"


# -- each provider's probe through its installed SDK -------------------------


def _http_lib_of(http_client) -> object:
    for cls in type(http_client).__mro__:
        package = (cls.__module__ or "").split(".")[0]
        if package in ("httpx", "httpx2"):
            return importlib.import_module(package)
    raise AssertionError("the SDK built neither an httpx nor an httpx2 client")


def _error_body(status: int) -> dict:
    return {
        "error": {
            "code": status,
            "message": f"provider said {status}",
            "status": "ERROR",
            "type": "error",
        }
    }


def _handler(http_lib, outcome: str, success_body, sent: list):
    def handle(request):
        sent.append(str(request.url))
        if outcome == "timeout":
            raise http_lib.ReadTimeout("timed out", request=request)
        if outcome == "400-then-ok" and len(sent) == 1:
            # A server that refuses JSON mode, then answers without it.
            return http_lib.Response(400, json=_error_body(400))
        if outcome in {"404", "401", "429", "500"}:
            return http_lib.Response(int(outcome), json=_error_body(int(outcome)))
        text = GOOD_REPLY if outcome in {"ok", "400-then-ok"} else PROSE_REPLY
        return http_lib.Response(200, json=success_body(text))

    return handle


def _openai_backend(outcome: str, sent: list):
    import openai

    from backend.processing.llm_backends.openai import OpenAILLMBackend

    http_lib = _http_lib_of(openai.OpenAI(api_key="sk-not-used")._client)

    def body(text):
        return {
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
        }

    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-test"
    backend.client = openai.OpenAI(
        api_key="sk-not-used",
        base_url="https://openai.example.test/v1",
        http_client=http_lib.Client(
            transport=http_lib.MockTransport(_handler(http_lib, outcome, body, sent))
        ),
    )
    return backend


def _anthropic_backend(outcome: str, sent: list):
    import anthropic

    from backend.processing.llm_backends.anthropic import AnthropicLLMBackend

    http_lib = _http_lib_of(anthropic.Anthropic(api_key="not-used")._client)

    def body(text):
        return {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-test"
    backend.client = anthropic.Anthropic(
        api_key="not-used",
        base_url="https://anthropic.example.test",
        http_client=http_lib.Client(
            transport=http_lib.MockTransport(_handler(http_lib, outcome, body, sent))
        ),
    )
    return backend


def _gemini_backend(outcome: str, sent: list, monkeypatch):
    from google import genai

    from backend.processing.llm_backends.gemini import GeminiLLMBackend

    monkeypatch.delenv("GOOGLE_GENAI_API_VERSION", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_HTTP_CLIENT", raising=False)
    real_client = genai.Client
    http_lib = _http_lib_of(real_client(api_key="not-used")._api_client._httpx_client)

    def body(text):
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": "STOP",
                }
            ]
        }

    transport = {
        "transport": http_lib.MockTransport(_handler(http_lib, outcome, body, sent))
    }

    def client_on_mock_transport(*args, http_options=None, **kwargs):
        options = genai.types.HttpOptions.model_validate(http_options or {})
        options = options.model_copy(update={"client_args": transport})
        return real_client(*args, http_options=options, **kwargs)

    monkeypatch.setattr(genai, "Client", client_on_mock_transport)
    return GeminiLLMBackend(api_key="not-used", model="gemini-test")


class _OllamaRequests:
    """Stands in for the ``requests`` module the Ollama backend calls."""

    def __init__(self, outcome: str, sent: list):
        self.outcome = outcome
        self.sent = sent

    def post(self, url, json=None, timeout=None, **kwargs):
        self.sent.append(url)
        if self.outcome == "timeout":
            raise requests.ReadTimeout("read timed out")
        response = requests.Response()
        response.url = url
        if self.outcome in {"404", "401", "429", "500"}:
            response.status_code = int(self.outcome)
            response._content = b'{"error": "model not found"}'
            return response
        text = GOOD_REPLY if self.outcome == "ok" else PROSE_REPLY
        response.status_code = 200
        response._content = json_module.dumps(
            {"message": {"role": "assistant", "content": text}, "done_reason": "stop"}
        ).encode()
        return response


json_module = json


def _ollama_backend(outcome: str, sent: list):
    from backend.processing.llm_backends.ollama import OllamaLLMBackend

    backend = OllamaLLMBackend(
        api_url="http://10.0.0.5:11434",
        model="llama-test",
        context_window=8192,
        allow_private_api_url=True,
        think=False,
    )
    backend.requests = _OllamaRequests(outcome, sent)
    return backend


EXPECTED_KIND = {
    "ok": "ok",
    "404": "unknown_model",
    "401": "auth",
    "429": "quota",
    "timeout": "timeout",
    "prose": "bad_response",
    "500": "error",
}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "ollama"])
@pytest.mark.parametrize("outcome", list(EXPECTED_KIND))
def test_each_provider_probe_reports_the_kind_of_failure(
    provider, outcome, monkeypatch
) -> None:
    if provider == "ollama" and outcome == "401":
        pytest.skip("Ollama takes no credential")
    sent: list = []
    if provider == "openai":
        backend = _openai_backend(outcome, sent)
    elif provider == "anthropic":
        backend = _anthropic_backend(outcome, sent)
    elif provider == "gemini":
        backend = _gemini_backend(outcome, sent, monkeypatch)
    else:
        backend = _ollama_backend(outcome, sent)

    result = probe_one(
        lambda model: backend,
        provider,
        backend.model,
        "main",
        include_detail=False,
        timeout=5,
    )

    assert result.kind == EXPECTED_KIND[outcome]
    assert result.ok is (outcome == "ok")
    assert backend.model in result.message
    # One generation each, with the SDK clients built as production builds them
    # (default retries): the probe turns retries off, so a 429, a 500 or a
    # timeout costs one request. Ollama may first read the model's facts from
    # /api/show, as every real call does.
    generations = [url for url in sent if not url.endswith("/api/show")]
    assert len(generations) == 1, sent
    if provider == "gemini":
        assert "/v1beta/models/gemini-test:generateContent" in generations[0]
    if provider == "ollama":
        assert generations[0].endswith("/api/chat")


def test_an_ollama_timeout_says_the_model_may_still_be_loading() -> None:
    sent: list = []
    backend = _ollama_backend("timeout", sent)

    result = probe_one(
        lambda model: backend, "ollama", "llama-test", "main", include_detail=False
    )

    assert "60 seconds" in result.message
    assert "still be loading" in result.message


# -- the route ---------------------------------------------------------------


class _FakeBackend:
    """A backend whose probe outcome depends on the model it was built for."""

    def __init__(self, model, calls: dict, refuse_key: bool = False):
        self.model = model
        self.api_key = "the-stored-key"
        self._calls = calls
        self._refuse_key = refuse_key

    def validate_api_key(self):
        if self._refuse_key:
            raise RuntimeError("401 key refused")
        return True

    def list_models(self):
        return ["good", "missing"]

    def probe_model(self, prompt, *, timeout, max_output_tokens):
        self._calls.setdefault(self.model, 0)
        self._calls[self.model] += 1
        if self.model == "missing":
            error = RuntimeError(
                "404 model not found at https://user:pw@provider.example.test with the-stored-key"
            )
            error.status_code = 404  # type: ignore[attr-defined]
            raise error
        return GOOD_REPLY


def _route_app(monkeypatch, *, public: bool, calls: dict, refuse_key=False, seen=None):
    async def _permission(db, request):
        return None if public else SimpleNamespace(id=1, role="owner")

    def _get_llm_backend(provider, api_key=None, model=None, **kwargs):
        if seen is not None:
            seen.append((provider, api_key, model))
        return _FakeBackend(model, calls, refuse_key=refuse_key)

    async def _db() -> AsyncGenerator[object, None]:
        yield object()

    monkeypatch.setattr(setup, "check_setup_permission", _permission)
    monkeypatch.setattr(setup, "get_llm_backend", _get_llm_backend)
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _db
    return app


async def _post(app, body: dict):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        return await client.post("/api/v1/setup/validate-llm", json=body)


@pytest.mark.anyio
async def test_a_refused_key_is_still_a_400(monkeypatch) -> None:
    calls: dict = {}
    app = _route_app(monkeypatch, public=False, calls=calls, refuse_key=True)

    response = await _post(
        app, {"provider": "openai", "probe_models": [{"model": "good"}]}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": setup.PUBLIC_LLM_VALIDATION_ERROR_DETAIL}
    assert calls == {}


@pytest.mark.anyio
async def test_a_failing_model_answers_200_with_its_result_row(monkeypatch) -> None:
    calls: dict = {}
    app = _route_app(monkeypatch, public=False, calls=calls)

    response = await _post(
        app,
        {
            "provider": "openai",
            "probe_models": [
                {"model": "good", "role": "main"},
                {"model": "missing", "role": "meeting_edge"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["results"][0] == {
        "model": "good",
        "role": "main",
        "ok": True,
        "kind": "ok",
        "message": "good answered.",
    }
    failed = body["results"][1]
    assert failed["model"] == "missing"
    assert failed["role"] == "meeting_edge"
    assert failed["ok"] is False
    assert failed["kind"] == "unknown_model"
    assert failed["message"].startswith("OpenAI does not serve missing.")
    # An administrator sees the provider's reason, with no URL or key in it.
    assert "404 model not found" in failed["message"]
    assert "pw@" not in failed["message"]
    assert "the-stored-key" not in failed["message"]
    assert body["message"] == "1 of 2 OpenAI models failed the test."


@pytest.mark.anyio
async def test_a_public_request_never_carries_provider_text(monkeypatch) -> None:
    calls: dict = {}
    app = _route_app(monkeypatch, public=True, calls=calls)

    response = await _post(
        app, {"provider": "openai", "probe_models": [{"model": "missing"}]}
    )

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["kind"] == "unknown_model"
    assert row["message"] == "OpenAI does not serve missing. Choose another model."
    assert "provider" not in response.text.replace("provider_role", "")


@pytest.mark.anyio
async def test_no_model_is_probed_twice_and_no_more_than_three(monkeypatch) -> None:
    calls: dict = {}
    app = _route_app(monkeypatch, public=False, calls=calls)

    response = await _post(
        app,
        {
            "provider": "gemini",
            "probe_models": [
                {"model": "a", "role": "main"},
                {"model": "a", "role": "meeting_edge"},
                {"model": "b"},
                {"model": "c"},
                {"model": "d"},
            ],
        },
    )

    assert response.status_code == 200
    assert calls == {"a": 1, "b": 1, "c": 1}
    assert [row["model"] for row in response.json()["results"]] == ["a", "b", "c"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "label"),
    [("gemini", "Gemini"), ("openai", "OpenAI"), ("anthropic", "Anthropic")],
)
@pytest.mark.parametrize("probe_models", [[], [{"model": "  "}]])
async def test_without_models_only_the_key_is_checked(
    monkeypatch, provider, label, probe_models
) -> None:
    calls: dict = {}
    app = _route_app(monkeypatch, public=False, calls=calls)

    response = await _post(app, {"provider": provider, "probe_models": probe_models})

    # Says what ran, not that a model passed (#1005).
    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "message": f"{label} is reachable. Choose a model to test it.",
        "results": [],
    }
    assert calls == {}


@pytest.mark.anyio
async def test_ollama_without_models_lists_them_and_asks_for_one(monkeypatch) -> None:
    monkeypatch.setattr(
        setup.config_manager,
        "get",
        lambda key, default=None: None if key == "ollama_api_url" else default,
    )
    calls: dict = {}
    app = _route_app(monkeypatch, public=True, calls=calls)

    response = await _post(app, {"provider": "ollama"})

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "message": "Ollama is reachable. Choose a model to test it.",
        "models": ["good", "missing"],
        "results": [],
    }
    assert calls == {}


@pytest.mark.anyio
async def test_the_secondary_is_tested_with_its_own_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "primary-key")
    monkeypatch.setenv("SECONDARY_OPENAI_API_KEY", "secondary-key")
    calls: dict = {}
    seen: list = []
    app = _route_app(monkeypatch, public=False, calls=calls, seen=seen)

    primary = await _post(app, {"provider": "openai"})
    secondary = await _post(
        app,
        {
            "provider": "openai",
            "provider_role": "secondary",
            "probe_models": [{"model": "good"}],
        },
    )

    assert primary.status_code == secondary.status_code == 200
    assert {api_key for _, api_key, _ in seen[:1]} == {"primary-key"}
    assert {api_key for _, api_key, _ in seen[1:]} == {"secondary-key"}
    assert secondary.json()["valid"] is True


def test_probe_timeouts_match_the_decision() -> None:
    assert model_probe.probe_timeout("gemini") == 20
    assert model_probe.probe_timeout("ollama") == 60


def test_openai_retries_without_json_mode_only_after_a_refusal() -> None:
    sent: list = []
    backend = _openai_backend("400-then-ok", sent)

    result = probe_one(
        lambda model: backend, "openai", backend.model, "main", include_detail=False
    )

    assert result.kind == "ok"
    assert len(sent) == 2, sent


def test_a_refused_connection_is_an_error_not_a_timeout() -> None:
    from urllib3.exceptions import NewConnectionError

    refused = NewConnectionError(None, "Failed to establish a new connection: refused")
    try:
        try:
            raise refused
        except NewConnectionError as inner:
            raise requests.ConnectionError("connection refused") from inner
    except requests.ConnectionError as outer:
        assert classify_probe_error(outer) == "error"

    try:
        try:
            raise ConnectionRefusedError(111, "Connection refused")
        except ConnectionRefusedError as inner:
            raise RuntimeError("Ollama API error (probe)") from inner
    except RuntimeError as outer:
        assert classify_probe_error(outer) == "error"


class _RecordingOllama:
    """Stands in for ``requests`` under a real OllamaLLMBackend built by the route."""

    def __init__(self):
        self.chats: list = []

    def get(self, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {"models": [{"name": "fallback-model"}]}
        ).encode()
        return response

    def post(self, url, json=None, **kwargs):
        response = requests.Response()
        response.status_code = 200
        if url.endswith("/api/show"):
            body = {
                "capabilities": ["completion", "thinking"],
                "model_info": {"general.architecture": "x", "x.context_length": 131072},
            }
        else:
            self.chats.append((url, json))
            body = {
                "message": {"role": "assistant", "content": GOOD_REPLY},
                "done_reason": "stop",
            }
        response._content = json_module.dumps(body).encode()
        return response


@pytest.mark.anyio
async def test_the_ollama_fallback_is_probed_with_its_own_window_and_thinking(
    monkeypatch,
) -> None:
    stored = {
        "ollama_api_url": "http://10.0.0.5:11434",
        "ollama_context_window": 32768,
        "ollama_think": True,
        "secondary_ollama_api_url": "http://10.0.0.6:11434",
        "secondary_ollama_context_window": 8192,
        "secondary_ollama_think": False,
    }
    monkeypatch.setattr(
        setup.config_manager, "get", lambda key, default=None: stored.get(key, default)
    )
    fake = _RecordingOllama()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)

    async def _permission(db, request):
        return SimpleNamespace(id=1, role="owner")

    async def _db() -> AsyncGenerator[object, None]:
        yield object()

    monkeypatch.setattr(setup, "check_setup_permission", _permission)
    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _db

    response = await _post(
        app,
        {
            "provider": "ollama",
            "provider_role": "secondary",
            "probe_models": [{"model": "fallback-model"}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True
    [(url, payload)] = fake.chats
    assert url == "http://10.0.0.6:11434/api/chat"
    assert payload["options"]["num_ctx"] == 8192
    assert payload["think"] is False


@pytest.mark.anyio
async def test_a_hosted_fallback_without_its_own_key_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "primary-key")
    monkeypatch.delenv("SECONDARY_OPENAI_API_KEY", raising=False)
    calls: dict = {}
    seen: list = []
    app = _route_app(monkeypatch, public=False, calls=calls, seen=seen)

    for path, body in (
        (
            "/api/v1/setup/validate-llm",
            {"provider_role": "secondary", "probe_models": [{"model": "good"}]},
        ),
        ("/api/v1/setup/list-models", {"provider_role": "secondary"}),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.post(path, json={"provider": "openai", **body})
        assert response.status_code == 400
        assert response.json() == {
            "detail": "No fallback key is configured. Set SECONDARY_OPENAI_API_KEY in .env."
        }
    assert seen == [] and calls == {}


@pytest.mark.anyio
async def test_the_model_list_reads_the_fallbacks_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "primary-key")
    monkeypatch.setenv("SECONDARY_OPENAI_API_KEY", "secondary-key")
    seen: list = []
    app = _route_app(monkeypatch, public=False, calls={}, seen=seen)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/setup/list-models",
            json={"provider": "openai", "provider_role": "secondary"},
        )

    assert response.status_code == 200
    assert [api_key for _, api_key, _ in seen] == ["secondary-key"]
