import sqlite3
from datetime import UTC, date, datetime

import pytest
import redis.connection

from backend.celery_app import celery_app
from backend.tests.redis_guard import (
    harden_celery_retry_policies,
    record_and_refuse,
    transcripts_route_modules,
)


def _adapt_date(value: date) -> str:
    return value.isoformat()


def _adapt_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(sep=" ")


sqlite3.register_adapter(date, _adapt_date)
sqlite3.register_adapter(datetime, _adapt_datetime)


# No test has a Redis to talk to, and every caller of one swallows its own
# failure, so an unstubbed dispatch is silent and slow rather than a test
# failure. Bound the retries here; the guard below turns the silence into a
# failure. See backend/tests/redis_guard.py for why each policy is needed.
harden_celery_retry_policies()


@pytest.fixture(autouse=True)
def redis_contact_guard(monkeypatch):
    """Fail any test that opens a real Redis connection.

    Bounded retries stop a leaked dispatch from being slow, but not from being
    wrong. Every Redis caller in this codebase is best-effort and swallows its
    own failure, so the test still passes while silently exercising the
    Redis-unavailable path instead of the one it means to test. This trips at
    the connection layer, records the attempt, and fails the test in teardown,
    where the caller's `except Exception` cannot reach it.

    The broker, the result backend and every application client (rate limiting,
    download progress, the chat relay) funnel through
    `redis.connection.AbstractConnection.connect`, so the seam is
    binding-agnostic: re-splitting a module cannot route around it.

    Yields the recorded call sites. A test that means to exercise the
    Redis-unavailable path can assert on them and then clear the list to
    acknowledge the contact; anything left at teardown fails the test.
    """
    attempts: list[str] = []
    monkeypatch.setattr(
        redis.connection.AbstractConnection, "connect", record_and_refuse(attempts)
    )

    yield attempts

    if attempts:
        pytest.fail(
            f"Test opened a real Redis connection ({len(attempts)} attempt(s)). "
            "Take the stub_celery_dispatch fixture for a Celery dispatch, or "
            "stub the client the code below uses. Last call site:\n\n"
            f"{attempts[-1]}",
            pytrace=False,
        )


class _StubbedTask:
    """The bare shape of the AsyncResult callers use: an id they log or return."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


@pytest.fixture
def stub_celery_dispatch(monkeypatch):
    """Record Celery dispatches instead of publishing them.

    Patches the attribute on the shared app object rather than any module's
    imported name, so it holds however the caller reached the dispatcher.
    Yields the recorded ``(name, args, kwargs)`` tuples, so a test that wants
    to assert on a dispatch can read them rather than build its own stub.
    """
    dispatched: list[tuple[str, object, object]] = []

    def _send_task(name, args=None, kwargs=None, **options):
        dispatched.append((name, args, kwargs))
        return _StubbedTask(f"stub-task-{len(dispatched)}")

    monkeypatch.setattr(celery_app, "send_task", _send_task)
    return dispatched


@pytest.fixture
def stub_meeting_edge_dispatch(monkeypatch):
    """Neutralise Meeting Edge refresh dispatch across every transcripts module.

    Mutating a transcript queues a Meeting Edge refresh. That is best-effort
    background work, irrelevant to what the transcript tests assert, and it is
    the dispatch that made the suite slow. Returns the patched module names.
    """

    async def _noop(*args, **kwargs) -> None:
        return None

    patched = []
    for module in transcripts_route_modules():
        if hasattr(module, "_dispatch_meeting_edge_refresh"):
            monkeypatch.setattr(module, "_dispatch_meeting_edge_refresh", _noop)
            patched.append(module.__name__)

    # A stub that patches nothing is the failure this fixture exists to prevent.
    assert patched, "No _dispatch_meeting_edge_refresh binding found to stub"
    return patched


class FakeSingleFlightRedis:
    """Just enough Redis for the single-flight guard, in memory.

    Only `SET NX EX` and the compare-and-delete release script are modelled,
    because those are the only two operations the guard performs. TTLs are
    recorded rather than enforced: no test wants to wait one out, and the ones
    that care about expiry drop the key directly.
    """

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.closed = 0

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def eval(self, _script, _numkeys, key, token):
        # The guard's release: delete only if we still hold the token.
        if self.keys.get(key) == token:
            del self.keys[key]
            self.ttls.pop(key, None)
            return 1
        return 0

    def close(self):
        self.closed += 1


@pytest.fixture
def fake_single_flight(monkeypatch):
    """Give the single-flight guard an in-memory Redis.

    Without this the guard hits the connection refusal above, falls through to
    its fail-open path, and the test silently exercises "Redis is down" instead
    of the behaviour it means to check. Yields the fake so a test can inspect
    or pre-seed the held keys.
    """
    from backend.core import single_flight as single_flight_module

    fake = FakeSingleFlightRedis()
    monkeypatch.setattr(single_flight_module, "_open_client", lambda: fake)
    return fake
