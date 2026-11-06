"""Cancelled notes stop at real provider request, repair and retry boundaries.

Only SDK/HTTP edges are faked. The real provider classes, prompts, parsers,
parts orchestration and secondary-provider wrapper run unchanged.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, create_engine
from sqlmodel import Session, select

from backend.core import db
from backend.core.encryption import encrypt_secret
from backend.models.cli_oauth import CliOAuthCredential
from backend.processing.cli.manager import CliConversationManager
from backend.processing.cli_backend import CliLLMBackend
from backend.processing.llm_backends.anthropic import AnthropicLLMBackend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.processing.llm_backends.gemini import GeminiLLMBackend
from backend.processing.llm_backends.openai import OpenAILLMBackend
from backend.tests.test_notes_cancel import database as database
from backend.tests.test_notes_cancel import engine as engine
from backend.tests.test_ollama_context_window import (
    FITS,
    _answer,
    _backend,
    _FakeOllama,
    _refusal,
    _Response,
)
from backend.tests.test_ollama_long_meeting import TRANSCRIPT, _Ollama
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.generation_claim import NOTES_RUN
from backend.utils.generation_run import acquire_run, run_check
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceRequest
from backend.utils.notes_cancellation import (
    NotesRunCancelled,
    checking_notes_run,
)
from backend.utils.time import utc_now


def _cancelled() -> None:
    raise NotesRunCancelled("run retired")


@pytest.fixture
def cli_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep the real CLI credential reader on a real, isolated test database."""
    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    CliOAuthCredential.__table__.create(engine)
    monkeypatch.setattr(db, "sync_engine", engine)
    try:
        yield
    finally:
        engine.dispose()


def _sdk_backend(provider: str, request: Callable[..., Any]) -> Any:
    classes = {
        "openai": OpenAILLMBackend,
        "anthropic": AnthropicLLMBackend,
        "gemini": GeminiLLMBackend,
    }
    backend = object.__new__(classes[provider])
    backend.model = "test-model"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=request)),
        messages=SimpleNamespace(stream=request),
        models=SimpleNamespace(generate_content=request),
    )
    return backend


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "ollama", "cli"])
@pytest.mark.parametrize("automatic", [False, True], ids=["manual", "automatic"])
@pytest.mark.usefixtures("cli_database")
def test_a_cancelled_run_does_not_start_a_provider_request(
    provider: str, automatic: bool
) -> None:
    requests: list[dict[str, Any]] = []

    def outbound(**kwargs: Any) -> None:
        requests.append(kwargs)
        raise RuntimeError("this request must never start")

    if provider == "ollama":
        network = _FakeOllama(_answer(FITS))
        backend = _backend(network, context_window=8192)
    elif provider == "cli":
        # The real manager must never reach credential resolution after Cancel.
        backend = CliLLMBackend(model="test-model")
    else:
        backend = _sdk_backend(provider, outbound)

    with checking_notes_run(_cancelled), pytest.raises(NotesRunCancelled):
        if automatic:
            backend.generate_meeting_intelligence(
                AutomaticMeetingIntelligenceRequest(
                    resolved_transcript="Hello.", unresolved_speakers=()
                )
            )
        else:
            backend.generate_meeting_notes("Hello.", {})

    assert not requests
    if provider == "ollama":
        assert not network.chat_payloads


class _CancellingOllama(_Ollama):
    def __init__(self, mode: str) -> None:
        super().__init__(window=8192)
        self.mode = mode
        self.cancelled = False

    def check(self) -> None:
        if self.cancelled:
            _cancelled()

    def post(self, url: str, **kwargs: Any) -> _Response:
        response = super().post(url, **kwargs)
        if not url.endswith("/api/chat"):
            return response
        prompt = kwargs["json"]["messages"][-1]["content"]
        if self.mode == "combine-retry" and "# Notes of the Parts" not in prompt:
            return response
        self.cancelled = True
        if self.mode == "http-retry":
            return _refusal({"error": "temporary model failure"}, status_code=503)
        if self.mode == "combine-retry":
            return _answer({**FITS, "done_reason": "length"})
        if self.mode in {"repair", "draft-retry"}:
            return _answer(FITS, "")
        return response


@pytest.mark.parametrize(
    "mode", ["repair", "http-retry", "part", "draft-retry", "combine-retry"]
)
def test_ollama_does_not_request_another_part_repair_or_retry_after_cancel(
    mode: str,
) -> None:
    network = _CancellingOllama(mode)
    backend = _backend(network, context_window=8192)
    with checking_notes_run(network.check), pytest.raises(NotesRunCancelled):
        if mode == "repair":
            backend.generate_meeting_intelligence(
                AutomaticMeetingIntelligenceRequest(
                    resolved_transcript="Hello.", unresolved_speakers=()
                )
            )
        else:
            backend.generate_meeting_notes_in_parts(
                TRANSCRIPT,
                {},
                overflow=ContextWindowExceededError("too long", window=8192),
            )

    if mode == "combine-retry":
        combines = [
            payload
            for payload in network.chat_payloads
            if "# Notes of the Parts" in payload["messages"][-1]["content"]
        ]
        assert len(combines) == 1
    else:
        assert len(network.chat_payloads) == 1


def test_anthropic_does_not_step_down_its_output_ceiling_after_cancel() -> None:
    cancelled = False
    requests: list[int] = []

    def stream(**kwargs: Any) -> None:
        nonlocal cancelled
        requests.append(kwargs["max_tokens"])
        cancelled = True
        raise RuntimeError(
            "max_tokens: 128000 > 4096, which is the maximum allowed for this model"
        )

    def check() -> None:
        if cancelled:
            _cancelled()

    backend = _sdk_backend("anthropic", stream)
    with checking_notes_run(check), pytest.raises(NotesRunCancelled):
        backend.generate_meeting_notes("Hello.", {})

    assert len(requests) == 1


@pytest.mark.parametrize(
    "cancel_in_primary", [False, True], ids=["before-secondary", "in-primary"]
)
def test_cancellation_never_calls_a_secondary_or_restores_the_primary_error(
    cancel_in_primary: bool,
) -> None:
    cancelled = False
    requests: list[str] = []

    def primary(**kwargs: Any) -> None:
        nonlocal cancelled
        requests.append("primary")
        cancelled = True
        if cancel_in_primary:
            _cancelled()
        raise RuntimeError("primary unavailable")

    def secondary(**kwargs: Any) -> None:
        requests.append("secondary")
        raise RuntimeError("secondary must never start")

    def check() -> None:
        if cancelled and not cancel_in_primary:
            _cancelled()

    chain = SecondaryLLMBackend(
        _sdk_backend("openai", primary), _sdk_backend("gemini", secondary)
    )
    with checking_notes_run(check), pytest.raises(NotesRunCancelled):
        chain.generate_meeting_notes("Hello.", {})

    assert requests == ["primary"]


def test_nested_run_checks_restore_outer_and_do_not_leak() -> None:
    network = _FakeOllama(_answer(FITS), _answer(FITS))
    backend = _backend(network, context_window=8192)

    with checking_notes_run(_cancelled):
        with checking_notes_run(lambda: None):
            backend.generate_meeting_notes("Inner run.", {})
        with pytest.raises(NotesRunCancelled):
            backend.generate_meeting_notes("Cancelled outer run.", {})

    backend.generate_meeting_notes("Independent call.", {})
    assert len(network.chat_payloads) == 2


def _real_hosted_backend(provider: str, transport: Any, http: Any) -> Any:
    """Use the installed SDK and a fake network, including the SDK's retry loop."""
    client = http.Client(transport=transport)
    backend = object.__new__(
        {
            "openai": OpenAILLMBackend,
            "anthropic": AnthropicLLMBackend,
            "gemini": GeminiLLMBackend,
        }[provider]
    )
    backend.model = "test-model"
    if provider == "openai":
        from openai import OpenAI

        backend.client = OpenAI(api_key="not-used", http_client=client, max_retries=1)
    elif provider == "anthropic":
        from anthropic import Anthropic

        backend.client = Anthropic(
            api_key="not-used", http_client=client, max_retries=1
        )
    else:
        from google import genai

        backend.client = genai.Client(
            api_key="not-used",
            http_options={
                "httpx_client": client,
                "retry_options": {
                    "attempts": 2,
                    "initial_delay": 0.001,
                    "max_delay": 0.001,
                    "jitter": 0,
                },
            },
        )
    return backend


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_owned_cloud_notes_recover_from_a_transient_503(
    provider: str, engine: Engine
) -> None:
    """The installed SDK retries a transient failure before returning real notes."""
    requests: list[Any] = []
    http = httpx
    if provider == "anthropic":
        # Anthropic 1.x uses httpx2; older supported releases use httpx.
        from anthropic import _base_client

        http = getattr(_base_client, "httpx2", httpx)

    answer = json.dumps(
        {
            "speaker_mapping": {},
            "title": "Budget",
            "notes_markdown": "## Summary\nAgreed the budget.",
        }
    )

    def wire(request: Any) -> Any:
        requests.append(request)
        if len(requests) == 1:
            return http.Response(
                503,
                json={
                    "error": {
                        "type": "overloaded_error",
                        "message": "Unavailable",
                        "code": 503,
                    }
                },
                headers={"retry-after-ms": "1"},
            )
        if provider == "openai":
            return http.Response(
                200,
                json={
                    "id": "chat-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        if provider == "gemini":
            return http.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": answer}]},
                            "finishReason": "STOP",
                        }
                    ]
                },
            )
        events = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg-1",
                        "type": "message",
                        "role": "assistant",
                        "model": "test-model",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 0},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": answer},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 20},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        stream = "".join(
            f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
        )
        return http.Response(
            200, content=stream.encode(), headers={"content-type": "text/event-stream"}
        )

    backend = _real_hosted_backend(provider, http.MockTransport(wire), http)
    try:
        with Session(engine) as session:
            transcript = acquire_run(session, 1, NOTES_RUN)
            assert transcript is not None
            session.commit()
            with checking_notes_run(
                run_check(session, 1, transcript.notes_run_id, NOTES_RUN)
            ):
                result = backend.generate_meeting_intelligence(
                    AutomaticMeetingIntelligenceRequest(
                        resolved_transcript="We agreed the budget.",
                        unresolved_speakers=(),
                    )
                )
        assert "Agreed the budget" in result.notes_markdown
        assert len(requests) == 2
    finally:
        backend.client.close()


@pytest.mark.parametrize("streaming", [False, True], ids=["notes", "streaming-sibling"])
@pytest.mark.usefixtures("cli_database")
def test_cli_cancelled_during_token_refresh_never_launches_the_model(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    cancelled = False
    requests: list[str] = []
    with Session(db.sync_engine) as session:
        session.add(
            CliOAuthCredential(
                id=1,
                user_id=1,
                provider="claude_code",
                status="active",
                access_token_encrypted=encrypt_secret("expired-access"),
                refresh_token_encrypted=encrypt_secret("old-refresh"),
                token_expires_at=utc_now() - timedelta(minutes=1),
            )
        )
        session.commit()

    async def refresh(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal cancelled
        requests.append("refresh")
        cancelled = True
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "fresh-refresh",
                "expires_in": 28800,
            },
        )

    def query(**kwargs: Any) -> AsyncIterator[Any]:
        requests.append("model")
        raise AssertionError(
            "The CLI model was launched after cancellation during refresh"
        )

    sdk = ModuleType("claude_agent_sdk")
    for name in (
        "AssistantMessage",
        "RateLimitEvent",
        "ResultMessage",
        "StreamEvent",
        "TextBlock",
    ):
        setattr(sdk, name, type(name, (), {}))
    sdk.ClaudeSDKError = RuntimeError
    sdk.ClaudeAgentOptions = SimpleNamespace
    sdk.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(httpx.AsyncClient, "post", refresh)

    def check() -> None:
        if cancelled:
            _cancelled()

    with checking_notes_run(check), pytest.raises(NotesRunCancelled):
        if streaming:
            list(CliConversationManager().stream_single_turn(1, "Hello."))
        else:
            CliLLMBackend(model="test-model", user_id=1).generate_meeting_notes(
                "Hello.", {}
            )

    assert requests == ["refresh"]
    with Session(db.sync_engine) as session:
        credential = session.exec(select(CliOAuthCredential)).one()
        assert credential.last_refreshed_at is not None
