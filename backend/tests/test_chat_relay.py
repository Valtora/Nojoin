"""Unit tests for the CLI chat worker->API Redis relay.

Uses an in-test dict-backed fake redis (no fakeredis / pytest-asyncio added),
in the spirit of the repo's other fakes. Asserts the relayed SSE frames are
byte-identical to the inline chat generator and that the relay bounds hangs.
"""

from __future__ import annotations

import asyncio
import json

import backend.services.chat_relay as chat_relay
from backend.services.chat_relay import ChatStreamPublisher


class _FakeStore:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.expiries: dict[str, int] = {}


class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self._ops = []

    def rpush(self, key, value):
        self._ops.append(("rpush", key, value))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        for op, key, val in self._ops:
            if op == "rpush":
                self.store.lists.setdefault(key, []).append(val)
            else:
                self.store.expiries[key] = val
        self._ops = []


class _FakeSyncRedis:
    def __init__(self, store):
        self.store = store

    def pipeline(self):
        return _FakePipeline(self.store)

    def close(self):
        pass


class _FakeAsyncRedis:
    def __init__(self, store):
        self.store = store

    async def blpop(self, key, timeout=0):
        items = self.store.lists.get(key)
        if items:
            return (key, items.pop(0))
        return None  # empty -> simulate a blocking-pop timeout

    async def aclose(self):
        pass


def _collect(task_id):
    async def _run():
        return [frame async for frame in chat_relay.relay_sse_frames(task_id)]

    return asyncio.run(_run())


def _use_async_store(monkeypatch, store):
    monkeypatch.setattr(
        chat_relay.aioredis, "from_url", lambda *a, **k: _FakeAsyncRedis(store)
    )


# --- publisher ---


def test_publisher_pushes_envelopes_and_refreshes_ttl(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(
        chat_relay.redis, "from_url", lambda *a, **k: _FakeSyncRedis(store)
    )
    pub = ChatStreamPublisher("t1")
    pub.publish_token("Hel")
    pub.publish_token("lo")
    pub.publish_done()

    key = "nojoin:cli_chat:t1"
    assert [json.loads(x) for x in store.lists[key]] == [
        {"t": "tok", "v": "Hel"},
        {"t": "tok", "v": "lo"},
        {"t": "done"},
    ]
    assert store.expiries[key] == 900


def test_publisher_skips_empty_tokens(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(
        chat_relay.redis, "from_url", lambda *a, **k: _FakeSyncRedis(store)
    )
    pub = ChatStreamPublisher("t1")
    pub.publish_token("")
    assert store.lists.get("nojoin:cli_chat:t1") is None


# --- relay framing (must stay byte-identical to the inline generator) ---


def test_relay_streams_tokens_then_done(monkeypatch):
    store = _FakeStore()
    store.lists["nojoin:cli_chat:t1"] = [
        json.dumps({"t": "tok", "v": "Hel"}),
        json.dumps({"t": "tok", "v": "lo"}),
        json.dumps({"t": "done"}),
    ]
    _use_async_store(monkeypatch, store)

    assert _collect("t1") == [
        'data: {"token": "Hel"}\n\n',
        'data: {"token": "lo"}\n\n',
        "data: [DONE]\n\n",
    ]


def test_relay_handles_single_whole_message(monkeypatch):
    store = _FakeStore()
    store.lists["nojoin:cli_chat:t1"] = [
        json.dumps({"t": "tok", "v": "Whole answer."}),
        json.dumps({"t": "done"}),
    ]
    _use_async_store(monkeypatch, store)

    assert _collect("t1") == [
        'data: {"token": "Whole answer."}\n\n',
        "data: [DONE]\n\n",
    ]


def test_relay_emits_error_then_done(monkeypatch):
    store = _FakeStore()
    store.lists["nojoin:cli_chat:t1"] = [json.dumps({"t": "err", "v": "boom"})]
    _use_async_store(monkeypatch, store)

    assert _collect("t1") == ['data: {"error": "boom"}\n\n', "data: [DONE]\n\n"]


# --- anti-hang: task dies / never produces ---


def test_relay_reports_failure_when_task_dies(monkeypatch):
    store = _FakeStore()  # empty list -> blpop returns None
    _use_async_store(monkeypatch, store)

    class _FailedResult:
        def __init__(self, *a, **k):
            pass

        @property
        def state(self):
            return "FAILURE"

    monkeypatch.setattr("celery.result.AsyncResult", _FailedResult)

    assert _collect("t1") == [
        chat_relay._error_frame(chat_relay._GENERIC_ERROR),
        "data: [DONE]\n\n",
    ]


def test_relay_bails_at_deadline_when_task_pending(monkeypatch):
    store = _FakeStore()
    _use_async_store(monkeypatch, store)
    monkeypatch.setattr(chat_relay, "_RELAY_DEADLINE_SECONDS", 10)

    class _PendingResult:
        def __init__(self, *a, **k):
            pass

        @property
        def state(self):
            return "PENDING"

    monkeypatch.setattr("celery.result.AsyncResult", _PendingResult)

    assert _collect("t1") == [
        chat_relay._error_frame(chat_relay._DEADLINE_ERROR),
        "data: [DONE]\n\n",
    ]


# --- error copy ---


def test_friendly_chat_error_categories():
    assert (
        "rate limit"
        in chat_relay.friendly_chat_error(
            Exception("HTTP 429 Too Many Requests")
        ).lower()
    )
    assert (
        "high demand"
        in chat_relay.friendly_chat_error(Exception("503 overloaded")).lower()
    )
    assert (
        "too long"
        in chat_relay.friendly_chat_error(Exception("Request timeout hit")).lower()
    )
    assert (
        "internal error"
        in chat_relay.friendly_chat_error(Exception("something weird")).lower()
    )


def test_friendly_chat_error_passes_through_usage_limit():
    from backend.processing.cli.manager import CliUsageLimitError

    exc = CliUsageLimitError(
        "Your Claude subscription usage limit is reached; it resets around 15:30 UTC.",
        resets_at=None,
    )
    # A usage-limit error already carries a precise reset-aware message.
    assert chat_relay.friendly_chat_error(exc) == str(exc)
