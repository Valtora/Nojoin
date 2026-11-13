"""The live naming ask on the live calls' backoff, shared with Meeting Edge (#994).

The naming check and its ask run as the worker runs them, against the live
lines of ``live_naming_fixtures``, and Meeting Edge's refresh against its
stand-in session (``test_meeting_edge_toggle``). Their providers raise the
errors a provider would, and both features read and write one recording's
tier states in the in-memory Redis every test gets, on a clock the test moves.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

import backend.worker.tasks as tasks_module
from backend.processing import live_ai_backoff
from backend.processing.live_ai_backoff import (
    LiveAIUnavailable,
    live_ai_chain,
    live_ai_notices,
)
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.tests.live_naming_fixtures import ONE
from backend.tests.test_live_ai_backoff import PROVIDER_TEXT, _http_error
from backend.tests.test_live_ai_backoff import clock as clock
from backend.tests.test_live_ai_failures import _http_lib_of
from backend.tests.test_live_speaker_naming_api import (
    session_maker as session_maker,
)
from backend.tests.test_live_speaker_naming_llm import (
    AI_SETTINGS,
    _ask_records,
    _run_task,
    _seed_owner,
    _speak_for,
)
from backend.tests.test_meeting_edge_toggle import (
    _live_edge_session,
    _run_live_edge_refresh,
)
from backend.utils.speaker_name_suggestions import parse_speaker_inference_response
from backend.worker.tasks import live_naming_stage

RECORDING = 1
# The name ask's purpose, as the recording read gives it.
NAME_ASK = "live_naming"
STATE_KEY = f"{live_ai_backoff.KEY_PREFIX}{RECORDING}"


class Provider(LLMBackend):
    """A provider tier serving both features: answers, or raises its error."""

    def __init__(self, provider_id: str, model: str, error: Any = None) -> None:
        self.provider_id = provider_id
        self.model = model
        self.error = error
        self.edge_calls = 0
        self.name_calls = 0

    def _fail_if_told(self) -> None:
        error = self.error() if callable(self.error) else self.error
        if error is not None:
            raise error

    def generate_meeting_edge(
        self, request: Any, prompt_template: Any = None, timeout: Any = None
    ):
        self.edge_calls += 1
        self._fail_if_told()
        return f"guidance from {self.model}"

    def infer_speaker_suggestions(
        self, transcript: str, prompt_template: Any = None, **kwargs: Any
    ) -> Any:
        self.name_calls += 1
        self._fail_if_told()
        return parse_speaker_inference_response(
            '{"suggestions": []}', allowed_labels=kwargs["eligible_labels"]
        )


@pytest.fixture
def dispatched(fake_single_flight: Any, stub_celery_dispatch: list[Any]) -> list[Any]:
    """The tasks a naming check queues, with its single-flight keys in memory."""
    return stub_celery_dispatch


def _use(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: backend
    )


async def _check(session_maker: Any, dispatched: list[Any]) -> None:
    """One naming check, then the ask it queued, as the worker runs them."""
    start = len(dispatched)
    await _run_task(session_maker, live_naming_stage.refresh_live_speaker_names_task)
    for name, _, kwargs in dispatched[start:]:
        assert name == live_naming_stage.LIVE_NAMING_ASK_TASK
        await _run_task(
            session_maker,
            live_naming_stage.ask_live_speaker_names_task,
            **(kwargs or {}),
        )


def _stored() -> dict[str, dict]:
    # The in-memory Redis every test gets (conftest's fake_live_ai_redis).
    raw = live_ai_backoff._open_client().hashes.get(STATE_KEY, {})
    return {key: json.loads(value) for key, value in raw.items()}


# -- the two features, either one first ----------------------------------------


@pytest.mark.anyio
async def test_a_wait_the_name_ask_began_pauses_meeting_edge_and_says_why(
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    dispatched: list[Any],
    clock: Any,
) -> None:
    provider = Provider("openai", "gpt-test", lambda: _http_error(401))
    _use(monkeypatch, provider)
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)

    await _check(session_maker, dispatched)
    assert provider.name_calls == 1

    # Meeting Edge has stored no error of its own, yet calls no one: the
    # panel says why, once, and keeps the guidance it had.
    session = _live_edge_session()
    clock.now += 2
    _run_live_edge_refresh(monkeypatch, session, provider)
    assert session.commit_count == 1
    clock.now += 2
    _run_live_edge_refresh(monkeypatch, session, provider)

    assert provider.edge_calls == 0 and provider.name_calls == 1
    assert session.commit_count == 1
    transcript = session.transcript
    assert transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_ERROR
    assert transcript.meeting_edge_error_message == (
        "Meeting Edge is paused: OpenAI refused the API key for gpt-test. It "
        "stays off until the AI settings change."
    )
    assert transcript.meeting_edge_payload == {"summary": "Earlier guidance"}
    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert notice["purposes"] == ["meeting_edge", "live_naming"]
    assert notice["message"] == (
        "Meeting Edge and speaker name suggestions: OpenAI refused the API key "
        "for gpt-test. Nojoin has stopped using it until the AI settings change."
    )


@pytest.mark.anyio
async def test_a_wait_meeting_edge_began_skips_the_name_ask_and_keeps_it_due(
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    dispatched: list[Any],
    clock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = Provider("openai", "gpt-test", lambda: _http_error(500))
    session = _live_edge_session()
    _run_live_edge_refresh(monkeypatch, session, provider)
    assert provider.edge_calls == 1
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)

    clock.now += 2
    caplog.clear()
    with caplog.at_level(logging.INFO):
        await _check(session_maker, dispatched)

    # No call, no warning and no ask recorded: a skip is a metric.
    assert provider.name_calls == 0
    assert '"stage":"live_naming_ask_skipped"' in caplog.text
    assert '"reason":"backoff"' in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert await _ask_records(session_maker) == {}
    assert session.transcript.meeting_edge_error_message == (
        "Meeting Edge is paused: OpenAI returned a server error for gpt-test. "
        "Nojoin will keep trying."
    )
    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert notice["purposes"] == ["meeting_edge", "live_naming"]
    assert notice["message"] == (
        "Meeting Edge and speaker name suggestions: OpenAI returned a server "
        "error for gpt-test. Nojoin will try it again in 30 seconds."
    )

    # Once the wait is over, the label, still due, is asked at once.
    provider.error = None
    clock.now += 28
    await _check(session_maker, dispatched)

    assert provider.name_calls == 1
    assert ONE in await _ask_records(session_maker)
    assert live_ai_notices(RECORDING, viewer_is_admin=True) == []


@pytest.mark.anyio
async def test_a_healthy_fallback_answers_the_name_ask_while_the_primary_waits(
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    dispatched: list[Any],
    clock: Any,
) -> None:
    primary = Provider("openai", "gpt-test", lambda: _http_error(500))
    fallback = Provider("ollama", "fake-llm")
    chain = SecondaryLLMBackend(primary, fallback)
    session = _live_edge_session()
    _run_live_edge_refresh(monkeypatch, session, chain)
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)

    clock.now += 2
    await _check(session_maker, dispatched)

    assert (primary.edge_calls, primary.name_calls) == (1, 0)
    assert (fallback.edge_calls, fallback.name_calls) == (1, 1)
    assert ONE in await _ask_records(session_maker)
    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert notice["purposes"] == ["meeting_edge", "live_naming"]


@pytest.mark.anyio
async def test_a_failed_ask_logs_one_warning_and_stores_no_provider_text(
    session_maker: Any,
    monkeypatch: pytest.MonkeyPatch,
    dispatched: list[Any],
    clock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = Provider("openai", "gpt-test", lambda: _http_error(500))
    _use(monkeypatch, provider)
    await _seed_owner(session_maker, AI_SETTINGS)
    await _speak_for(session_maker, 61)

    with caplog.at_level(logging.INFO):
        await _check(session_maker, dispatched)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [r.name for r in warnings] == [live_ai_backoff.__name__]
    assert (
        warnings[0]
        .getMessage()
        .startswith(
            "Live naming ask for recording 1: OpenAI gpt-test (primary) failed "
            "(unavailable), trying it again in 30 s"
        )
    )
    (state,) = _stored().values()
    assert state["purposes"] == ["live_naming"] and state["kind"] == "unavailable"
    assert "req_abc123" not in json.dumps(live_ai_backoff._open_client().hashes)
    # The ask counts, as a failed ask always has.
    records = await _ask_records(session_maker)
    assert ONE in records and "req_abc123" not in json.dumps(records)
    notices = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert "req_abc123" not in json.dumps(notices, default=str)


# -- OpenAI's streamed ask failing part-way ---------------------------------------

LABEL = "LIVE_PROVISIONAL_1"


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _chunk(text: str) -> bytes:
    return _sse(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {"index": 0, "delta": {"content": text}, "finish_reason": None}
            ],
        }
    )


def _openai_streaming(outcome: str) -> tuple[Any, list[Any]]:
    """The real OpenAI backend whose streamed answer stops after one chunk."""
    import openai

    from backend.processing.llm_backends.openai import OpenAILLMBackend

    http_lib = _http_lib_of(openai.OpenAI(api_key="sk-not-used")._client)
    requests: list[Any] = []

    class PartAnswer(http_lib.SyncByteStream):
        def __init__(self, request: Any) -> None:
            self.request = request

        def __iter__(self):
            yield _chunk('{"suggestions": [{"diarization_label": "')
            if outcome == "read-timeout":
                raise http_lib.ReadTimeout("timed out", request=self.request)
            if outcome == "dropped":
                raise http_lib.RemoteProtocolError(
                    "peer closed connection without sending complete message body",
                    request=self.request,
                )
            if outcome == "error-event":
                yield _sse(
                    {"error": {"message": PROVIDER_TEXT, "type": "server_error"}}
                )
            # "cut-short": the stream ends with the answer half written.

    def respond(request: Any) -> Any:
        requests.append(request)
        return http_lib.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=PartAnswer(request),
        )

    backend = object.__new__(OpenAILLMBackend)
    backend.model = "gpt-test"
    backend.client = openai.OpenAI(
        api_key="sk-not-used",
        base_url="https://openai.example.test/v1",
        http_client=http_lib.Client(transport=http_lib.MockTransport(respond)),
    )
    return backend, requests


def _ask(backend: Any) -> Any:
    return live_ai_chain(
        backend, RECORDING, purpose=NAME_ASK
    ).infer_speaker_suggestions(
        f"[00:01] {LABEL}: Hello, I'm Alice Example.",
        timeout=90,
        user_notes=None,
        meeting_context=None,
        eligible_labels=[LABEL],
    )


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        ("read-timeout", "timeout"),
        ("dropped", "error"),
        ("error-event", "error"),
        ("cut-short", "bad_response"),
    ],
)
def test_a_stream_that_fails_part_way_backs_off_like_any_failure(
    clock: Any, outcome: str, kind: str
) -> None:
    """OpenAI streams the name ask, so its 90 s timeout is per read (#994).

    A stream that breaks after its first chunk is one failed call: it is
    classified, waits 30 s, and is not called again inside that wait.
    """
    backend, requests = _openai_streaming(outcome)

    with pytest.raises(LiveAIUnavailable):
        _ask(backend)
    clock.now += 29
    with pytest.raises(LiveAIUnavailable):
        _ask(backend)

    assert len(requests) == 1
    (state,) = _stored().values()
    assert state["kind"] == kind and state["stopped"] is False
    assert state["until"] == clock.now - 29 + 30
    assert state["purposes"] == ["live_naming"]
    assert "req_abc123" not in json.dumps(live_ai_backoff._open_client().hashes)
