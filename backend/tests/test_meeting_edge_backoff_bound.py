"""How many provider calls a 53 minute capture makes against a failing provider.

#994 saw about 1,500 failed calls to each provider in a 53 minute capture: a
Meeting Edge refresh every 2 s, each calling the primary and then the fallback.
These run the real refresh task for the same capture, a trigger every 2 s on a
clock the test moves, and count the calls each provider receives. With both
features on, each trigger also runs the real live naming ask, due every time:
as often as it could ever run.

The module imports nothing the backoff added, so the counts can be compared
with a checkout that has none.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import backend.worker.tasks as tasks_module
from backend.processing.llm_backends.base import LLMBackend
from backend.processing.llm_backends.factory import SecondaryLLMBackend
from backend.tests.test_meeting_edge_toggle import (
    _live_edge_session,
    _run_live_edge_refresh,
)
from backend.worker.tasks import live_naming_stage

CAPTURE_SECONDS = 53 * 60
TRIGGER_EVERY_SECONDS = 2
TRIGGERS = CAPTURE_SECONDS // TRIGGER_EVERY_SECONDS


def _http_error(status: int, headers: dict | None = None) -> Exception:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    try:
        raise requests.HTTPError("provider said no", response=response)
    except requests.HTTPError as inner:
        try:
            raise RuntimeError("Ollama API error (Meeting Edge)") from inner
        except RuntimeError as outer:
            return outer


class Provider(LLMBackend):
    def __init__(
        self,
        provider_id: str,
        model: str,
        status: int | None = None,
        headers: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self.status = status
        self.headers = headers
        self.calls = 0
        self.name_calls = 0

    def generate_meeting_edge(
        self, request: Any, prompt_template: Any = None, timeout: Any = None
    ):
        self.calls += 1
        if self.status is not None:
            raise _http_error(self.status, self.headers)
        return "Guidance"

    def infer_speaker_suggestions(
        self, transcript: str, prompt_template: Any = None, **kwargs: Any
    ) -> Any:
        self.name_calls += 1
        if self.status is not None:
            raise _http_error(self.status, self.headers)
        return None


def _ask_on_every_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name ask with a label due every time, its database reads stood in."""
    stage = live_naming_stage
    monkeypatch.setattr(stage, "live_name_suggestions_enabled", lambda s: True)
    monkeypatch.setattr(stage, "owner_settings", lambda session, user_id: {})
    monkeypatch.setattr(
        stage, "_live_llm_config", lambda session, rid: SimpleNamespace(provider="x")
    )
    monkeypatch.setattr(
        stage,
        "load_recording_timeline_notes",
        lambda session, rid: SimpleNamespace(user_notes_list=lambda: None),
    )
    line = {"start": 0.0, "end": 2.0, "speaker": "LIVE_PROVISIONAL_1", "text": "Hi."}
    monkeypatch.setattr(
        stage,
        "mark_live_asks",
        lambda session, rid: (["LIVE_PROVISIONAL_1"], [line], None),
    )
    monkeypatch.setattr(stage, "store_live_llm_suggestions", lambda *a, **k: 0)


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    primary: Provider,
    fallback: Provider | None,
    *,
    names: bool = False,
) -> None:
    """A trigger every 2 s for 53 minutes, each a full refresh.

    With ``names``, each trigger also runs the name ask, first on every other
    trigger, since either of a publication's tasks can reach the worker first.
    """
    clock = {"now": 1_000_000.0}
    backoff = sys.modules.get("backend.processing.live_ai_backoff")
    if backoff is not None:
        monkeypatch.setattr(backoff, "_now", lambda: clock["now"])
    session = _live_edge_session()
    chain = primary if fallback is None else SecondaryLLMBackend(primary, fallback)
    if names:
        _ask_on_every_trigger(monkeypatch)
    edge = tasks_module.refresh_meeting_edge_task
    ask = live_naming_stage.ask_live_speaker_names_task
    originals = (edge._session, ask._session)
    edge._session = ask._session = session
    try:
        for trigger in range(TRIGGERS):
            if trigger:
                clock["now"] += TRIGGER_EVERY_SECONDS
            if names and trigger % 2:
                ask.run(1)
            if trigger:
                edge.run(1)
            else:
                _run_live_edge_refresh(monkeypatch, session, chain)
            if names and not trigger % 2:
                ask.run(1)
    finally:
        edge._session, ask._session = originals


def test_a_failing_primary_with_a_healthy_fallback(monkeypatch, fake_single_flight):
    primary = Provider("ollama", "fake-500", status=500)
    fallback = Provider("ollama", "fake-llm")

    _capture(monkeypatch, primary, fallback)

    # 0, 30, 90, 210, 450 s, then every 5 minutes: 14 calls, not 1,590.
    assert primary.calls == 14
    assert fallback.calls == TRIGGERS


def test_both_providers_rate_limited(monkeypatch, fake_single_flight):
    primary = Provider("ollama", "fake-429", status=429, headers={"Retry-After": "60"})
    fallback = Provider(
        "ollama", "fake-429b", status=429, headers={"Retry-After": "60"}
    )

    _capture(monkeypatch, primary, fallback)

    assert primary.calls <= 53 and fallback.calls <= 53


def test_a_refused_key_on_both_providers(monkeypatch, fake_single_flight):
    primary = Provider("anthropic", "claude-x", status=401)
    fallback = Provider("ollama", "fake-401", status=401)

    _capture(monkeypatch, primary, fallback)

    assert (primary.calls, fallback.calls) == (1, 1)


def test_both_features_against_one_failing_provider(monkeypatch, fake_single_flight):
    provider = Provider("ollama", "fake-500", status=500)

    _capture(monkeypatch, provider, None, names=True)

    # One backoff, whichever feature calls: the 14 calls Meeting Edge makes
    # on its own, where the name ask used to add one on every trigger.
    assert provider.calls + provider.name_calls == 14


def test_both_features_with_a_failing_primary_and_a_healthy_fallback(
    monkeypatch, fake_single_flight
):
    primary = Provider("ollama", "fake-500", status=500)
    fallback = Provider("ollama", "fake-llm")

    _capture(monkeypatch, primary, fallback, names=True)

    assert primary.calls + primary.name_calls == 14
    assert (fallback.calls, fallback.name_calls) == (TRIGGERS, TRIGGERS)


def test_both_features_against_a_refused_key_on_both_providers(
    monkeypatch, fake_single_flight
):
    primary = Provider("anthropic", "claude-x", status=401)
    fallback = Provider("ollama", "fake-401", status=401)

    _capture(monkeypatch, primary, fallback, names=True)

    assert primary.calls + primary.name_calls == 1
    assert fallback.calls + fallback.name_calls == 1


class SlowTimeout(Provider):
    """Takes its whole 90 s timeout to fail, on every call."""

    def __init__(self, clock: dict) -> None:
        super().__init__("anthropic", "claude-x")
        self.clock = clock

    def generate_meeting_edge(
        self, request: Any, prompt_template: Any = None, timeout: Any = None
    ):
        self.calls += 1
        self.clock["now"] += 90
        raise TimeoutError("timed out")


def test_a_provider_that_times_out_on_every_call(monkeypatch, fake_single_flight):
    """Each wait counts from the failure, 90 s after the call began.

    Triggers that arrive during a call are dropped by the refresh's guard, so
    the next one is 2 s after the call returns.
    """
    clock = {"now": 1_000_000.0}
    backoff = sys.modules.get("backend.processing.live_ai_backoff")
    if backoff is not None:
        monkeypatch.setattr(backoff, "_now", lambda: clock["now"])
    provider = SlowTimeout(clock)
    session = _live_edge_session()
    end = clock["now"] + CAPTURE_SECONDS
    _run_live_edge_refresh(monkeypatch, session, provider)
    task = tasks_module.refresh_meeting_edge_task
    original = task._session
    task._session = session
    try:
        while clock["now"] < end:
            clock["now"] += TRIGGER_EVERY_SECONDS
            task.run(1)
    finally:
        task._session = original

    # Calls at 0, 120, 270, 480 and 810 s, then every 390 s (a 300 s wait
    # after each 90 s timeout).
    assert provider.calls == 11
