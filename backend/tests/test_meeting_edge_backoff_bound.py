"""How many provider calls a 53 minute capture makes against a failing provider.

#994 saw about 1,500 failed calls to each provider in a 53 minute capture: a
Meeting Edge refresh every 2 s, each calling the primary and then the fallback.
These run the real refresh task for the same capture, a trigger every 2 s on a
clock the test moves, and count the calls each provider receives.

The module imports nothing the backoff added, so the counts can be compared
with a checkout that has none.
"""

from __future__ import annotations

import sys
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

    def generate_meeting_edge(
        self, request: Any, prompt_template: Any = None, timeout: Any = None
    ):
        self.calls += 1
        if self.status is not None:
            raise _http_error(self.status, self.headers)
        return "Guidance"


def _capture(
    monkeypatch: pytest.MonkeyPatch, primary: Provider, fallback: Provider
) -> None:
    """A trigger every 2 s for 53 minutes, each a full refresh."""
    clock = {"now": 1_000_000.0}
    backoff = sys.modules.get("backend.processing.live_ai_backoff")
    if backoff is not None:
        monkeypatch.setattr(backoff, "_now", lambda: clock["now"])
    session = _live_edge_session()
    chain = SecondaryLLMBackend(primary, fallback)
    _run_live_edge_refresh(monkeypatch, session, chain)
    task = tasks_module.refresh_meeting_edge_task
    original = task._session
    task._session = session
    try:
        for _ in range(TRIGGERS - 1):
            clock["now"] += TRIGGER_EVERY_SECONDS
            task.run(1)
    finally:
        task._session = original


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
