"""Ollama thinks by default, so Nojoin has to say whether it should.

Measured on Ollama 0.40.11: with thinking on, qwen3.5:4b gave no answer token
within 900 s on one part of a long meeting's notes, and with ``think: false``
the same part finished in 98 s, under the 300 s the product allows. The
``ollama_think`` setting (off by default) is therefore sent on every
``/api/chat`` request, but only to a model whose ``/api/show`` capabilities
include ``thinking``.

Each test resolves the setting the way the product does, through
``_merge_llm_config`` and ``get_llm_backend_with_secondary``, and fakes only the
HTTP transport to Ollama.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
import requests
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user, get_db
from backend.main import create_app
from backend.processing.llm_backends import ollama as ollama_module
from backend.processing.llm_backends.factory import (
    SecondaryLLMBackend,
    get_llm_backend_with_secondary,
)
from backend.utils.config_manager import config_manager
from backend.utils.llm_config import LLM_PURPOSE_DEFAULT, _merge_llm_config

URL = "http://host.docker.internal:11434"
FALLBACK_URL = "http://host.docker.internal:22434"
THINKING = ["completion", "tools", "thinking"]
PLAIN = ["completion", "tools"]


class _Response:
    def __init__(self, body: dict, status_code: int = 200, lines=()):
        self._body = body
        self.status_code = status_code
        self._lines = lines

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)

    def json(self) -> dict:
        return self._body

    def iter_lines(self):
        for line in self._lines:
            yield json.dumps(line).encode()


class _Ollama:
    """The transport: /api/show per model, /api/chat from a queue."""

    def __init__(self, capabilities: dict[str, list[str] | None]):
        self.capabilities = capabilities
        self.chats: list[dict] = []
        self.answers: list[_Response] = []

    def post(self, url, json=None, **kwargs):
        if url.endswith("/api/show"):
            capabilities = self.capabilities.get(json["model"])
            if capabilities is None:
                return _Response({"error": "unavailable"}, status_code=500)
            return _Response({"capabilities": capabilities})
        self.chats.append(json)
        if self.answers:
            return self.answers.pop(0)
        return _Response({"message": {"content": "Friday."}, "done": True})


@pytest.fixture
def ollama(monkeypatch) -> _Ollama:
    fake = _Ollama({"think-model": THINKING, "plain-model": PLAIN})
    monkeypatch.setattr(requests, "post", fake.post)
    real_get = config_manager.get
    addresses = {"ollama_api_url": URL, "secondary_ollama_api_url": FALLBACK_URL}
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: addresses.get(key) or real_get(key, default),
    )
    ollama_module._model_facts_cache.clear()
    yield fake
    ollama_module._model_facts_cache.clear()


def _base(**overrides) -> dict:
    return {
        "llm_provider": "ollama",
        "ollama_model": "think-model",
        "ollama_api_url": URL,
        **overrides,
    }


def _backend(**overrides):
    resolved = _merge_llm_config(
        base_config=_base(**overrides),
        system_keys={},
        owner_settings=None,
        user_settings=None,
        purpose=LLM_PURPOSE_DEFAULT,
    )
    return get_llm_backend_with_secondary(resolved)


def _notes(backend) -> str:
    return backend.generate_meeting_notes("[00:00 - 00:04] Ana: hi", {}, timeout=5)


def _chat(backend) -> str:
    return backend.ask_question_about_meeting(
        user_question="What was decided?",
        meeting_notes="notes",
        diarized_transcript="[00:00 - 00:04] Ana: we ship Friday",
        timeout=5,
    )


def _stream(backend) -> str:
    return "".join(
        backend.ask_question_streaming(
            user_question="What was decided?",
            meeting_notes="notes",
            diarized_transcript="[00:00 - 00:04] Ana: we ship Friday",
            timeout=5,
        )
    )


def _title(backend) -> str:
    return backend.infer_meeting_title("[00:00 - 00:04] Ana: hi", timeout=5)


CALLERS = {
    "notes": _notes,
    "chat": _chat,
    "streaming-chat": _stream,
    "title": _title,
}


@pytest.mark.parametrize("caller", CALLERS.values(), ids=CALLERS.keys())
def test_thinking_is_off_unless_the_setting_turns_it_on(ollama, caller):
    caller(_backend())
    caller(_backend(ollama_think=False))
    caller(_backend(ollama_think=True))

    assert [chat["think"] for chat in ollama.chats] == [False, False, True]


@pytest.mark.parametrize("think", [False, True])
def test_a_model_that_cannot_think_is_sent_no_think_key(ollama, think):
    _chat(_backend(ollama_model="plain-model", ollama_think=think))

    assert "think" not in ollama.chats[0]


def test_unknown_capabilities_send_the_setting_anyway(ollama):
    """A refusal is reported; a reasoning model left thinking never answers."""
    backend_off = _backend(ollama_model="unreachable-model")
    backend_on = _backend(ollama_model="unreachable-model", ollama_think=True)

    _notes(backend_off)
    _notes(backend_on)

    assert [chat["think"] for chat in ollama.chats] == [False, True]


def test_the_retry_after_a_context_refusal_carries_think_too(ollama):
    refusal = {
        "error": json.dumps(
            {
                "error": {
                    "code": 400,
                    "message": "request (9000 tokens) exceeds the available "
                    "context size (8192 tokens), try increasing it",
                    "type": "exceed_context_size_error",
                    "n_prompt_tokens": 9000,
                    "n_ctx": 8192,
                }
            }
        )
    }
    ollama.answers.append(_Response(refusal, status_code=400))
    history = [
        {"role": "user" if i % 2 == 0 else "model", "parts": [{"text": f"{i}" * 2600}]}
        for i in range(4)
    ]
    backend = _backend(ollama_context_window=8192)

    assert (
        backend.ask_question_about_meeting(
            user_question="What was decided?",
            meeting_notes="notes",
            diarized_transcript="[00:00 - 00:04] Ana: we ship Friday",
            conversation_history=history,
            timeout=5,
        )
        == "Friday."
    )

    assert len(ollama.chats) == 2
    assert all(chat["think"] is False for chat in ollama.chats)


def test_the_fallback_follows_its_own_setting(ollama):
    backend = _backend(
        secondary_llm_provider="ollama",
        secondary_ollama_model="think-model",
        secondary_ollama_api_url=URL,  # Same server: a second private address is refused.
        secondary_ollama_think=True,
        ollama_think=False,
    )
    assert isinstance(backend, SecondaryLLMBackend)

    _title(backend._primary)
    _title(backend._secondary)

    assert [chat["think"] for chat in ollama.chats] == [False, True]


def test_the_answer_is_the_content_and_never_the_thinking(ollama):
    ollama.answers.append(
        _Response(
            {
                "message": {
                    "role": "assistant",
                    "thinking": "Let me work out the date first.",
                    "content": "Friday.",
                },
                "done": True,
            }
        )
    )

    assert _chat(_backend(ollama_think=True)) == "Friday."


def test_a_streamed_answer_leaves_the_thinking_out(ollama):
    ollama.answers.append(
        _Response(
            {},
            lines=[
                {"message": {"thinking": "Hmm.", "content": ""}, "done": False},
                {"message": {"thinking": " Friday.", "content": ""}, "done": False},
                {"message": {"content": "It ships"}, "done": False},
                {"message": {"content": " Friday."}, "done": False},
                {"message": {"content": ""}, "done": True, "done_reason": "stop"},
            ],
        )
    )

    assert _stream(_backend(ollama_think=True)) == "It ships Friday."


# --- GET /llm/thinking-support -------------------------------------------------


async def _thinking_support(model: str, **params) -> tuple[int, dict]:
    app = create_app(app_lifespan=None)

    async def override_get_db() -> AsyncGenerator[object, None]:
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="owner", is_superuser=True
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get(
            "/api/v1/llm/thinking-support", params={"model": model, **params}
        )
    return response.status_code, response.json()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("model", "supported"),
    [
        ("think-model", True),
        ("plain-model", False),
        ("unreachable-model", None),
    ],
    ids=["supported", "unsupported", "unknown"],
)
async def test_thinking_support_reports_what_ollama_says(ollama, model, supported):
    status, body = await _thinking_support(model)

    assert status == 200
    assert body == {"model": model, "supported": supported}


@pytest.mark.anyio
async def test_a_server_that_predates_capabilities_is_unknown(ollama, monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kw: _Response({"license": "MIT"}),
    )

    status, body = await _thinking_support("old-model")

    assert status == 200
    assert body["supported"] is None


@pytest.mark.anyio
async def test_the_fallback_is_asked_at_its_own_address(ollama, monkeypatch):
    asked: list[str] = []
    real_post = ollama.post

    def post(url, **kwargs):
        asked.append(url)
        return real_post(url, **kwargs)

    monkeypatch.setattr(requests, "post", post)

    await _thinking_support("think-model")
    await _thinking_support("think-model", secondary="true")

    assert asked[0].startswith("http://host.docker.internal:11434")
    assert asked[-1].startswith("http://host.docker.internal:22434")
