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
    BackoffStore,
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
# The name ask's purpose, as the recording read gives it.
NAME_ASK = "live_naming"
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

    def infer_speaker_suggestions(
        self, transcript: str, prompt_template: Any = None, **kwargs: Any
    ) -> Any:
        self.calls += 1
        error = self.error() if callable(self.error) else self.error
        if error is not None:
            raise error
        return f"names from {self.model}"


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


def _slow_timeout(clock: Clock):
    """A provider that takes its whole 90 s timeout to fail."""

    def fail() -> Exception:
        clock.now += 90
        return TimeoutError("timed out")

    return fail


def test_a_failure_waits_from_the_moment_it_failed(clock, fake_live_ai_redis, caplog):
    """A call that times out after 90 s starts its 30 s wait then."""
    with caplog.at_level(logging.WARNING, logger=live_ai_backoff.__name__):
        with pytest.raises(LiveAIUnavailable):
            _call(Provider("anthropic", "claude-x", _slow_timeout(clock)))

    (state,) = _states(fake_live_ai_redis).values()
    assert state["until"] == clock.now + 30
    (warning,) = [r for r in caplog.records if r.name == live_ai_backoff.__name__]
    assert "trying it again in 30 s" in warning.getMessage()


def test_the_next_tier_is_judged_when_the_one_before_it_failed(
    clock, fake_live_ai_redis
):
    primary = Provider("anthropic", "claude-x", lambda: _http_error(500))
    fallback = Provider(
        "ollama", "fake-llm", lambda: _http_error(429, {"Retry-After": "60"})
    )
    with pytest.raises(LiveAIUnavailable):
        _call(primary, fallback)
    fallback_until = clock.now + 60

    # At 30 s the primary may be called again and the fallback may not. The
    # primary then spends 90 s timing out, by when the fallback may be.
    primary.error = _slow_timeout(clock)
    fallback.error = None
    clock.now += 30
    assert _call(primary, fallback) == "guidance from fake-llm"

    assert clock.now > fallback_until and fallback.calls == 2
    (primary_state,) = _states(fake_live_ai_redis).values()
    assert primary_state["model"] == "claude-x"
    assert primary_state["until"] == clock.now + 60


def test_an_answering_tier_drops_a_later_tiers_wait(clock, fake_live_ai_redis):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    fallback = Provider("ollama", "fake-502", lambda: _http_error(502))
    with pytest.raises(LiveAIUnavailable):
        _call(primary, fallback)
    assert len(_states(fake_live_ai_redis)) == 2

    primary.error = None
    clock.now += 30
    chain = live_ai_chain(SecondaryLLMBackend(primary, fallback), RECORDING)
    assert (
        chain.generate_meeting_edge("request", timeout=90) == "guidance from fake-500"
    )

    assert _states(fake_live_ai_redis) == {}
    assert chain._states == {}
    assert live_ai_notices(RECORDING, viewer_is_admin=True) == []


def test_a_later_tier_that_is_stopped_stays_stopped(clock, fake_live_ai_redis):
    primary = Provider("ollama", "fake-500", lambda: _http_error(500))
    fallback = Provider("ollama", "fake-401", lambda: _http_error(401))
    with pytest.raises(LiveAIUnavailable):
        _call(primary, fallback)

    primary.error = None
    clock.now += 30
    assert _call(primary, fallback) == "guidance from fake-500"
    clock.now += 3600
    primary.error = lambda: _http_error(500)
    with pytest.raises(LiveAIUnavailable):
        _call(primary, fallback)

    assert fallback.calls == 1
    notices = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert {(n["model"], n["action"]) for n in notices} == {
        ("fake-500", "retrying"),
        ("fake-401", "stopped"),
    }


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


def test_ollama_refusing_a_model_is_not_about_a_key(clock):
    _fail(Provider("ollama", "fake-401", lambda: _http_error(401)))

    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)

    assert notice["message"] == (
        "Meeting Edge: Ollama refused access to fake-401. Nojoin has stopped "
        "using it until the AI settings change."
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


def _name_ask(provider: Provider) -> LiveAIChain:
    return live_ai_chain(provider, RECORDING, purpose=NAME_ASK)


def _ask_names(provider: Provider) -> None:
    try:
        _name_ask(provider).infer_speaker_suggestions("transcript", timeout=90)
    except LiveAIUnavailable:
        pass


def test_a_name_ask_failing_alone_is_named_on_its_own(clock):
    _ask_names(Provider("openai", "gpt-test", lambda: _http_error(429)))

    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)

    assert notice["purposes"] == ["live_naming"]
    assert notice["message"] == (
        "Speaker name suggestions: OpenAI is rate limiting gpt-test. Nojoin will "
        "try it again in 30 seconds."
    )


def test_a_notice_names_every_feature_the_wait_holds_up(
    clock, monkeypatch: pytest.MonkeyPatch
):
    provider = Provider("ollama", "fake-401", lambda: _http_error(401))
    _fail(provider)
    (alone,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    saves: list[str] = []
    save = BackoffStore.save
    monkeypatch.setattr(
        BackoffStore,
        "save",
        lambda store, key, state: (saves.append(key), save(store, key, state)),
    )

    # The name ask passes the stopped provider over, once and then again.
    for _ in range(2):
        clock.now += 2
        assert _name_ask(provider).skip_if_waiting() is True

    assert provider.calls == 1 and len(saves) == 1
    (both,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert both["purposes"] == ["meeting_edge", "live_naming"]
    assert both["message"] == (
        "Meeting Edge and speaker name suggestions: Ollama refused access to "
        "fake-401. Nojoin has stopped using it until the AI settings change."
    )
    # A new id, so the page shows it again with both features in it.
    assert both["id"] != alone["id"]


def test_a_failure_counts_on_from_the_other_features(clock, fake_live_ai_redis):
    """Both features' calls ran at once, and both failed: two failures."""
    failing = Provider("ollama", "fake-500", lambda: _http_error(500))
    slow = Provider(
        "ollama",
        "fake-500",
        _meanwhile(lambda: _fail(failing), _timeout_after(clock, 5)),
    )

    _ask_names(slow)

    (state,) = _states(fake_live_ai_redis).values()
    assert state["failures"] == 2 and state["until"] == clock.now + 60
    assert state["purposes"] == ["meeting_edge", "live_naming"]


def test_a_tier_that_answered_for_the_other_feature_starts_afresh(
    clock, fake_live_ai_redis
):
    errors: list[Any] = [_http_error(500), None, _http_error(500)]
    provider = Provider("ollama", "flaky", lambda: errors.pop())
    _fail(provider)
    clock.now += 30
    name_ask = _name_ask(provider)

    assert _call(provider) == "guidance from flaky"
    with pytest.raises(LiveAIUnavailable):
        name_ask.infer_speaker_suggestions("transcript", timeout=90)

    (state,) = _states(fake_live_ai_redis).values()
    assert state["failures"] == 1 and state["purposes"] == ["live_naming"]


def test_two_recordings_with_the_same_failing_tier_get_different_ids(clock):
    """The page's toast ids are global, so a notice id belongs to one recording."""
    for recording in (RECORDING, RECORDING + 1):
        try:
            live_ai_chain(
                Provider("ollama", "fake-401", lambda: _http_error(401)), recording
            ).generate_meeting_edge("request", timeout=90)
        except LiveAIUnavailable:
            pass

    (first,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    (second,) = live_ai_notices(RECORDING + 1, viewer_is_admin=True)

    assert first["message"] == second["message"]
    assert first["id"] != second["id"]
    assert live_ai_notices(RECORDING, viewer_is_admin=True)[0]["id"] == first["id"]


def test_a_notice_id_is_keyed_with_a_random_value(clock, fake_live_ai_redis):
    """A plain hash of a small integer id could be undone by trying them all."""
    _fail(Provider("ollama", "fake-401", lambda: _http_error(401)))
    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    secret = fake_live_ai_redis.values[live_ai_backoff.NOTICE_SECRET_KEY]

    assert len(secret) == 64 and len(notice["id"]) == 16
    del fake_live_ai_redis.values[live_ai_backoff.NOTICE_SECRET_KEY]
    assert live_ai_notices(RECORDING, viewer_is_admin=True)[0]["id"] != notice["id"]


# -- both features at once ------------------------------------------------------


def _feature(purpose: str, backend: Any) -> None:
    """One whole run of a feature's chain, as its task would make it."""
    chain = live_ai_chain(backend, RECORDING, purpose=purpose)
    method = (
        chain.infer_speaker_suggestions
        if purpose == NAME_ASK
        else chain.generate_meeting_edge
    )
    try:
        method("request", timeout=90)
    except LiveAIUnavailable:
        pass


def _meanwhile(run: Any, then: Any = None) -> Any:
    """A call that lets the other feature run to the end while it is in flight,
    then answers (``then`` None) or fails with ``then``."""
    pending = [run]

    def call() -> Any:
        while pending:
            pending.pop()()
        return then() if callable(then) else then

    return call


OVERLAPS = pytest.mark.parametrize(
    ("first", "second"), [("meeting_edge", NAME_ASK), (NAME_ASK, "meeting_edge")]
)


def _timeout_after(clock: Clock, seconds: float) -> Any:
    def fail() -> Exception:
        clock.now += seconds
        return TimeoutError("timed out")

    return fail


@OVERLAPS
def test_a_stop_set_during_the_other_features_call_stays(
    clock, fake_live_ai_redis, first, second
):
    """One feature's call times out while the other is refused the key."""
    refused = Provider("ollama", "shared", lambda: _http_error(401))
    slow = Provider(
        "ollama",
        "shared",
        _meanwhile(lambda: _feature(second, refused), _timeout_after(clock, 90)),
    )

    _feature(first, slow)

    (state,) = _states(fake_live_ai_redis).values()
    assert (state["stopped"], state["kind"], state["until"]) == (True, "auth", None)
    assert state["purposes"] == ["meeting_edge", "live_naming"]
    clock.now += 3600
    _feature(first, slow)
    assert slow.calls == 1


@OVERLAPS
def test_the_later_of_two_overlapping_waits_stands(
    clock, fake_live_ai_redis, first, second
):
    """One feature's call times out while the other is asked to wait an hour."""
    limited = Provider(
        "ollama", "shared", lambda: _http_error(429, {"Retry-After": "3600"})
    )
    slow = Provider(
        "ollama",
        "shared",
        _meanwhile(lambda: _feature(second, limited), _timeout_after(clock, 90)),
    )
    started = clock.now

    _feature(first, slow)

    (state,) = _states(fake_live_ai_redis).values()
    assert state["kind"] == "rate_limit" and state["until"] == started + 3600
    assert state["failures"] == 2
    (notice,) = live_ai_notices(RECORDING, viewer_is_admin=True)
    assert "is rate limiting shared" in notice["message"]


@OVERLAPS
def test_a_fallback_the_other_feature_put_into_a_wait_is_not_called(
    clock, fake_live_ai_redis, first, second
):
    """While the primary times out, the other feature fails on the fallback."""
    fallback = Provider("ollama", "fallback", lambda: _http_error(500))
    failing_primary = Provider("anthropic", "claude-x", lambda: _http_error(500))
    slow_primary = Provider(
        "anthropic",
        "claude-x",
        _meanwhile(
            lambda: _feature(second, SecondaryLLMBackend(failing_primary, fallback)),
            _timeout_after(clock, 10),
        ),
    )

    _feature(first, SecondaryLLMBackend(slow_primary, fallback))

    # The fallback's 30 s wait has 20 s to run when the primary fails.
    assert fallback.calls == 1
    states = _states(fake_live_ai_redis)
    assert {s["model"]: s["purposes"] for s in states.values()} == {
        "claude-x": ["meeting_edge", "live_naming"],
        "fallback": ["meeting_edge", "live_naming"],
    }


@OVERLAPS
def test_an_answer_leaves_a_later_tier_the_other_feature_stopped(
    clock, fake_live_ai_redis, first, second
):
    primary = Provider("anthropic", "claude-x", lambda: _http_error(500))
    fallback = Provider("ollama", "fallback", lambda: _http_error(500))
    _feature(first, SecondaryLLMBackend(primary, fallback))
    clock.now += 30
    refusing = Provider("ollama", "fallback", lambda: _http_error(401))
    answering = Provider(
        "anthropic",
        "claude-x",
        _meanwhile(lambda: _feature(second, SecondaryLLMBackend(primary, refusing))),
    )

    _feature(first, SecondaryLLMBackend(answering, fallback))

    assert [
        (state["model"], state["stopped"], state["kind"])
        for state in _states(fake_live_ai_redis).values()
    ] == [("fallback", True, "auth")]


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
