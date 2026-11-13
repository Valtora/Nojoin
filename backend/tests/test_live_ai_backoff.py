"""A failing provider backs off during a live capture (#994).

The chain runs real ``LLMBackend`` tiers whose calls raise the errors a
provider would, against the in-memory Redis every test gets
(``fake_live_ai_redis``) and a clock the test moves.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import backend.worker.tasks as tasks_module
from backend.processing import live_ai_backoff
from backend.processing.cli.manager import CliCredentialUnavailableError
from backend.processing.live_ai_backoff import (
    EPOCH_KEY,
    LiveAIChain,
    LiveAIUnavailable,
    backoff_seconds,
    bump_epoch,
    bump_epoch_on_io_start,
    chain_tiers,
    live_ai_chain,
    live_ai_notices,
)
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.tests.test_meeting_edge_toggle import (
    _live_edge_session,
    _run_live_edge_refresh,
)

RECORDING = 1
PROVIDER_TEXT = "request_id=req_abc123 the upstream said something private"


def _http_error(status: int, headers: dict | None = None) -> Exception:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    try:
        raise requests.HTTPError(PROVIDER_TEXT, response=response)
    except requests.HTTPError as inner:
        try:
            raise RuntimeError(f"Ollama API error (Meeting Edge): {inner}") from inner
        except RuntimeError as outer:
            return outer


class Provider(LLMBackend):
    """A provider tier: answers, or raises what it is told to, and counts."""

    def __init__(self, provider_id: str, model: str, error: Any = None) -> None:
        self.provider_id = provider_id
        self.model = model
        self.error = error
        self.calls = 0

    def generate_meeting_edge(
        self, request: Any, prompt_template: Any = None, timeout: Any = None
    ):
        self.calls += 1
        error = self.error() if callable(self.error) else self.error
        if error is not None:
            raise error
        return f"guidance from {self.model}"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(live_ai_backoff, "_now", clock)
    return clock


def _call(*tiers: Provider) -> Any:
    backend: Any = tiers[0]
    if len(tiers) == 2:
        backend = SecondaryLLMBackend(tiers[0], tiers[1])
    return live_ai_chain(backend, RECORDING).generate_meeting_edge(
        "request", timeout=90
    )


def _states(fake: Any) -> dict[str, dict]:
    raw = fake.hashes.get(f"{live_ai_backoff.KEY_PREFIX}{RECORDING}", {})
    return {key: json.loads(value) for key, value in raw.items()}


def test_backoff_doubles_from_30_seconds_to_5_minutes():
    assert [backoff_seconds(n) for n in range(1, 7)] == [30, 60, 120, 240, 300, 300]


def test_each_tier_has_its_role_in_the_chain():
    subscription = Provider("cli", "sonnet")
    primary = Provider("anthropic", "claude-x")
    fallback = Provider("ollama", "llama3")
    chain = SecondaryLLMBackend(subscription, SecondaryLLMBackend(primary, fallback))

    tiers = chain_tiers(chain)

    assert [(t.role, t.provider_id, t.model) for t in tiers] == [
        ("subscription", "cli", "sonnet"),
        ("primary", "anthropic", "claude-x"),
        ("fallback", "ollama", "llama3"),
    ]


def test_a_failing_primary_waits_while_the_fallback_answers(clock, fake_live_ai_redis):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    fallback = Provider("ollama", "fake-llm")

    assert _call(primary, fallback) == "guidance from fake-llm"
    clock.now += 29
    assert _call(primary, fallback) == "guidance from fake-llm"
    assert primary.calls == 1 and fallback.calls == 2

    clock.now += 1
    _call(primary, fallback)
    assert primary.calls == 2
    (state,) = _states(fake_live_ai_redis).values()
    assert state["kind"] == "unavailable" and state["failures"] == 2
    assert state["until"] == clock.now + 60


def test_a_tier_that_answers_starts_again_from_nothing(clock, fake_live_ai_redis):
    errors: list[Any] = [_http_error(500)]
    primary = Provider("ollama", "flaky", lambda: errors.pop() if errors else None)

    with pytest.raises(LiveAIUnavailable):
        _call(primary)
    clock.now += 30
    assert _call(primary) == "guidance from flaky"

    assert _states(fake_live_ai_redis) == {}


def test_a_rate_limit_waits_at_least_what_the_provider_asked(clock, fake_live_ai_redis):
    primary = Provider(
        "ollama", "fake-429", lambda: _http_error(429, {"Retry-After": "90"})
    )

    with pytest.raises(LiveAIUnavailable):
        _call(primary)

    (state,) = _states(fake_live_ai_redis).values()
    assert state["kind"] == "rate_limit" and state["until"] == clock.now + 90


def test_a_refused_key_stops_the_tier_until_the_settings_change(
    clock, fake_live_ai_redis
):
    primary = Provider("ollama", "fake-401", lambda: _http_error(401))

    with pytest.raises(LiveAIUnavailable):
        _call(primary)
    clock.now += 3600
    with pytest.raises(LiveAIUnavailable):
        _call(primary)
    assert primary.calls == 1

    bump_epoch()
    primary.error = None
    assert _call(primary) == "guidance from fake-401"
    assert primary.calls == 2


def test_a_waiting_chain_is_skipped_as_a_metric(clock, caplog):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    with pytest.raises(LiveAIUnavailable):
        _call(primary)

    chain = live_ai_chain(primary, RECORDING)
    with caplog.at_level(logging.INFO):
        assert chain.skip_if_waiting() is True

    assert '"stage":"meeting_edge_refresh_skipped"' in caplog.text
    assert '"reason":"backoff"' in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_each_failure_that_starts_or_extends_a_backoff_logs_one_warning(clock, caplog):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))

    with caplog.at_level(logging.WARNING, logger=live_ai_backoff.__name__):
        for _ in range(10):
            try:
                _call(primary)
            except LiveAIUnavailable:
                pass
            clock.now += 2

    warnings = [r for r in caplog.records if r.name == live_ai_backoff.__name__]
    assert primary.calls == 1 and len(warnings) == 1
    assert "trying it again in 30 s" in warnings[0].getMessage()


def test_the_paused_message_is_built_from_the_classified_reason(clock):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    fallback = Provider("anthropic", "claude-x", lambda: _http_error(401))

    with pytest.raises(LiveAIUnavailable) as raised:
        _call(primary, fallback)

    message = str(raised.value)
    assert message == (
        "Meeting Edge is paused: Ollama returned a server error for fake-500; "
        "Anthropic refused the API key for claude-x. Nojoin will keep trying."
    )
    assert "req_abc123" not in message and "private" not in message


def test_without_redis_the_call_runs_as_before(clock, fake_live_ai_redis):
    fake_live_ai_redis.unreachable = True
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    fallback = Provider("ollama", "fake-llm")

    for _ in range(3):
        assert _call(primary, fallback) == "guidance from fake-llm"

    assert primary.calls == 3
    assert live_ai_notices(RECORDING, viewer_is_admin=True) == []


# -- notices ------------------------------------------------------------------


def _fail(provider: Provider) -> None:
    try:
        LiveAIChain(
            provider, recording_id=RECORDING, purpose="meeting_edge"
        ).generate_meeting_edge("request", timeout=90)
    except LiveAIUnavailable:
        pass


def test_a_retrying_notice_says_when_nojoin_tries_again(clock):
    _fail(Provider("ollama", "fake-500", lambda: _http_error(500)))

    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=False)

    assert notice["action"] == "retrying" and notice["kind"] == "unavailable"
    assert notice["message"] == (
        "Meeting Edge: Ollama returned a server error for fake-500. Nojoin will "
        "try it again in 30 seconds."
    )
    assert notice["settings"] is None


@pytest.mark.parametrize(
    ("admin", "fixable_by", "settings", "ending"),
    [
        (
            True,
            "you",
            "ai-providers",
            "Nojoin has stopped using it until the AI settings change.",
        ),
        (
            False,
            "admin",
            None,
            "Nojoin has stopped using it. Ask your administrator to check the AI "
            "provider settings.",
        ),
    ],
)
def test_a_stopped_server_provider_is_worded_for_the_viewer(
    clock, admin, fixable_by, settings, ending
):
    _fail(Provider("anthropic", "claude-x", lambda: _http_error(401)))

    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=admin)

    assert notice["action"] == "stopped" and notice["retry_at"] is None
    assert notice["fixable_by"] == fixable_by and notice["settings"] == settings
    assert notice["message"] == (
        f"Meeting Edge: Anthropic refused the API key for claude-x. {ending}"
    )


class Subscription(Provider):
    @property
    def provider_label(self) -> str:
        return "Claude subscription"


def test_a_stopped_subscription_is_the_users_own_to_fix(clock):
    _fail(Subscription("cli", "sonnet", CliCredentialUnavailableError("reconnect")))

    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=False)

    assert notice["fixable_by"] == "you" and notice["settings"] == "your-ai"
    assert notice["message"] == (
        "Meeting Edge: your Claude subscription needs reconnecting. Nojoin has "
        "stopped using it until you reconnect it."
    )


def test_a_notice_keeps_its_id_and_leaves_when_its_provider_answers(clock):
    errors = [None, _http_error(500), _http_error(500)]
    provider = Provider("ollama", "flaky", lambda: errors.pop())

    _fail(provider)
    first = live_ai_notices(RECORDING, viewer_is_admin=True)[0]["id"]
    clock.now += 30
    _fail(provider)
    assert live_ai_notices(RECORDING, viewer_is_admin=True)[0]["id"] == first

    clock.now += 60
    _call(provider)
    assert live_ai_notices(RECORDING, viewer_is_admin=True) == []


# -- recovery -------------------------------------------------------------------


@pytest.mark.parametrize(("queues", "bumped"), [({"io"}, True), ({"gpu"}, False)])
def test_the_io_worker_starting_starts_afresh(fake_live_ai_redis, queues, bumped):
    sender = SimpleNamespace(
        app=SimpleNamespace(
            amqp=SimpleNamespace(
                queues=SimpleNamespace(consume_from={name: object() for name in queues})
            )
        )
    )

    bump_epoch_on_io_start(sender)

    assert (fake_live_ai_redis.get(EPOCH_KEY) == "1") is bumped


# -- the Meeting Edge refresh -----------------------------------------------


def test_a_refresh_with_every_provider_waiting_writes_nothing(
    monkeypatch, fake_single_flight, clock, caplog
):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    session = _live_edge_session()

    _run_live_edge_refresh(monkeypatch, session, primary)
    failed_commits = session.commit_count
    clock.now += 2
    with caplog.at_level(logging.INFO):
        _run_live_edge_refresh(monkeypatch, session, primary)

    assert primary.calls == 1
    assert session.commit_count == failed_commits
    assert '"reason":"backoff"' in caplog.text


def test_a_failed_refresh_stores_the_classified_reason(
    monkeypatch, fake_single_flight, clock
):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    session = _live_edge_session()

    _run_live_edge_refresh(monkeypatch, session, primary)

    transcript = session.transcript
    assert transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_ERROR
    assert transcript.meeting_edge_error_message == (
        "Meeting Edge is paused: Ollama returned a server error for fake-500. "
        "Nojoin will keep trying."
    )


def test_an_unexpected_failure_stores_no_provider_text(
    monkeypatch, fake_single_flight, clock
):
    def broken(self, request, *, timeout):
        raise ValueError(PROVIDER_TEXT)

    session = _live_edge_session()
    monkeypatch.setattr(LiveAIChain, "generate_meeting_edge", broken)

    _run_live_edge_refresh(monkeypatch, session, Provider("ollama", "fake-llm"))

    assert session.transcript.meeting_edge_error_message == (
        "Meeting Edge could not be updated."
    )


def test_without_redis_a_refresh_runs_and_raises_nothing(
    monkeypatch, fake_single_flight, fake_live_ai_redis, clock
):
    fake_live_ai_redis.unreachable = True
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    fallback = Provider("ollama", "fake-llm")
    session = _live_edge_session()

    for _ in range(3):
        _run_live_edge_refresh(
            monkeypatch, session, SecondaryLLMBackend(primary, fallback)
        )

    assert primary.calls == 3 and fallback.calls == 3
    assert (
        session.transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_READY
    )
