import os

# No test downloads a model (#689). Offline mode makes huggingface_hub refuse
# every request at once, and fastembed too. Without it, fastembed downloaded
# the text embedding model when the host had network and retried for 78 s when
# it had none. Set ahead of the imports below, because huggingface_hub reads it
# once, on import.
os.environ["HF_HUB_OFFLINE"] = "1"

# Points NOJOIN_DATA_DIR at a temporary directory. Imported ahead of the
# backend, which fixes its data paths on import; data_dir.py says why. Without
# it the suite wrote into the checkout's data/, and a read-only checkout failed
# on import.
from backend.tests.data_dir import SUITE_DATA_DIR

# isort: split
import shutil
import sqlite3
import sys
import tempfile
import traceback
import types
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import redis.asyncio.connection
import redis.connection

# Relationships name their targets by string, so a module that builds its own
# schema fails with NoReferencedTableError when run alone unless every model is
# registered first. The full suite hid this behind whichever module imported
# the registry earlier.
import backend.models.registry  # noqa: F401
from backend.celery_app import celery_app
from backend.processing.text_embedding_version import TEXT_EMBEDDING_DIMENSIONS
from backend.tests.network_guard import NetworkGuard, server_hostnames
from backend.tests.postgres_setup import POSTGRES_TEST_URL_ENV, drop_session_objects
from backend.tests.redis_guard import (
    app_frames,
    harden_celery_retry_policies,
    record_and_refuse,
    transcripts_route_modules,
)
from backend.utils import stored_paths


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
    download progress, the chat relay) funnel through the synchronous or
    asynchronous `AbstractConnection.connect`, so re-splitting a module
    cannot route around the guard.

    Yields the recorded call sites. A test that means to exercise the
    Redis-unavailable path can assert on them and then clear the list to
    acknowledge the contact; anything left at teardown fails the test.
    """
    attempts: list[str] = []
    monkeypatch.setattr(
        redis.connection.AbstractConnection, "connect", record_and_refuse(attempts)
    )
    monkeypatch.setattr(
        redis.asyncio.connection.AbstractConnection,
        "connect",
        record_and_refuse(attempts),
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


@pytest.fixture(autouse=True)
def rate_limits_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select the limiter's real in-memory implementation without a Redis probe.

    Older upload/auth tests implicitly selected this path after failed async
    Redis contact, which the synchronous-only guard could not see. Keep their
    limits and assertions intact while making that boundary explicit.
    """
    from backend.utils import rate_limit

    async def no_redis() -> None:
        return None

    monkeypatch.setattr(rate_limit, "_get_redis", no_redis)
    rate_limit._fallback_windows.clear()
    rate_limit._fallback_concurrency.clear()


@pytest.fixture(autouse=True)
def stub_transcript_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[int, int]]:
    """Ordinary ORM tests record hints; transport tests replace Redis clients."""
    from backend.utils import transcript_publication

    published: list[dict[int, int]] = []
    monkeypatch.setattr(
        transcript_publication, "publish_transcript_changes", published.append
    )
    return published


@pytest.fixture(autouse=True)
def network_contact_guard(monkeypatch):
    """Fail any test that resolves or connects to a host outside the suite.

    A test may reach loopback, a Unix socket and the hosts in the suite's
    server URLs (``NOJOIN_TEST_POSTGRES_URL``). Anything else is refused: a
    real LLM server, a calendar or telemetry endpoint, a public name looked up
    in DNS. A test written against a real Ollama URL then fails on every
    network, instead of calling the server when one answers and passing on the
    unreachable path when none does. The callers catch their own failures, so
    the attempt is recorded and the test fails in teardown.

    Yields the guard. A test of the guard reads ``guard.hosts`` and calls
    ``guard.clear()`` to acknowledge what it caused. ``network_guard.py`` has
    the seams and what they cannot see.
    """
    guard = NetworkGuard(server_hostnames())
    guard.install(monkeypatch.setattr)

    yield guard

    message = guard.failure_message()
    if message:
        pytest.fail(message, pytrace=False)


@pytest.fixture(autouse=True)
def stored_files_may_live_in_temp(monkeypatch):
    """Let recordings and documents live in the system temp directory too.

    Tests keep their media under ``tmp_path``, or name a path under it that
    they never create, where the stored-path check
    (``backend/utils/stored_paths.py``) would refuse it. A test of that check
    sets the roots itself, and can take the unpatched functions from here.
    """
    real = (stored_paths.recording_roots, stored_paths.document_roots)
    temp = Path(tempfile.gettempdir())
    monkeypatch.setattr(stored_paths, "recording_roots", lambda: (*real[0](), temp))
    monkeypatch.setattr(stored_paths, "document_roots", lambda: (*real[1](), temp))
    return real


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


@pytest.fixture(autouse=True)
def text_embedding_model_guard(request, monkeypatch):
    """Fail any test that loads the real text embedding model.

    Offline mode, set at the top of this file, stops fastembed downloading the
    model, but every caller of the embedding service catches the failure and
    carries on with nothing indexed. A test that forgets a stub still passes,
    on the failure path. So fastembed is replaced for the test by a module
    whose ``TextEmbedding`` records the call and raises, and the test fails in
    teardown, where the caller's ``except Exception`` cannot reach it.

    Take ``stub_text_embedding`` for vectors. Tests of ``TextEmbeddingService``
    install their own fastembed over this one. A test that means to load the
    real model from the model cache takes ``@pytest.mark.real_text_embedding``;
    offline mode still holds, so nothing is downloaded.
    """
    if request.node.get_closest_marker("real_text_embedding"):
        yield []
        return

    attempts: list[str] = []

    def _refuse(*args, **kwargs):
        attempts.append(app_frames(traceback.format_stack()[:-1]))
        raise RuntimeError(
            "A test tried to load the real text embedding model. Take the "
            "stub_text_embedding fixture instead."
        )

    guard = types.ModuleType("fastembed")
    guard.TextEmbedding = _refuse
    monkeypatch.setitem(sys.modules, "fastembed", guard)

    yield attempts

    if attempts:
        pytest.fail(
            "Test loaded the real text embedding model "
            f"({len(attempts)} attempt(s)). Take the stub_text_embedding "
            "fixture, or mark the test real_text_embedding to load it from the "
            f"model cache. Last call site:\n\n{attempts[-1]}",
            pytrace=False,
        )


class FakeTextEmbeddingService:
    """The text embedding service's ``embed``, with one fixed vector per text.

    The vector has the model's width, so it fits ``context_chunks.embedding``
    on Postgres too, and unit length, so a cosine distance to it is defined.
    ``calls`` holds the texts of each call.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: str | list[str]) -> list[list[float]]:
        batch = [texts] if isinstance(texts, str) else list(texts)
        self.calls.append(batch)
        return [[1.0] + [0.0] * (TEXT_EMBEDDING_DIMENSIONS - 1) for _ in batch]


@pytest.fixture
def stub_text_embedding(monkeypatch):
    """Give every caller of the text embedding service a fake one.

    Each caller imports ``get_text_embedding_service`` from its module when it
    runs, so patching the module attribute reaches them all. Returns the fake,
    so a test can read what was embedded.
    """
    from backend.processing import text_embedding

    fake = FakeTextEmbeddingService()
    monkeypatch.setattr(text_embedding, "get_text_embedding_service", lambda: fake)
    return fake


# Cache overrides inherited from the host must not escape the temporary HOME.
MODEL_CACHE_VARIABLES = (
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "HF_HOME",
    "XDG_CACHE_HOME",
    "FASTEMBED_CACHE_PATH",
)


@pytest.fixture(autouse=True)
def model_cache_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Isolate every test's model caches, including Nemotron's pending marker.

    Keep the environment unset so cache-precedence tests can still exercise
    each override themselves. Returns the temporary HOME, as before.
    """
    from huggingface_hub import constants

    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for variable in MODEL_CACHE_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    # ONNX/Pyannote loaders can omit cache_dir. huggingface_hub freezes these
    # defaults on import, before a function-scoped environment patch runs.
    hub = str(home / ".cache" / "huggingface" / "hub")
    monkeypatch.setattr(constants, "HF_HUB_CACHE", hub)
    monkeypatch.setattr(constants, "HUGGINGFACE_HUB_CACHE", hub)
    return home


@pytest.fixture(scope="session", autouse=True)
def suite_data_dir() -> Iterator[Path]:
    """The data directory the whole session shares, under the temp directory.

    data_dir.py points NOJOIN_DATA_DIR at it, imported at the top of this
    file because the backend fixes its data paths on import. There is one per
    session because
    config_manager loads config.json once. A test that needs a data directory
    of its own patches path_manager onto tmp_path, as the keyring and
    telemetry tests do. Removed when the session ends.
    """
    yield SUITE_DATA_DIR
    shutil.rmtree(SUITE_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def postgres_test_url() -> str:
    """URL of a real PostgreSQL to run driver-specific tests against.

    Most of the suite runs on SQLite, which cannot stand in for Postgres on
    everything: the bind parameter ceiling that broke finalize on a two-hour
    recording is a limit of the Postgres wire protocol and of asyncpg's use of
    it, and SQLite builds accept far more variables than Postgres will. A test
    that needs the real ceiling has to talk to a real server.

    Skips when the environment names no server, so a plain `pytest` on a host
    without Postgres still passes. CI sets this for the backend suite, so the
    tests that depend on it do run before merge.
    """
    url = os.getenv(POSTGRES_TEST_URL_ENV)
    if not url:
        pytest.skip(
            f"{POSTGRES_TEST_URL_ENV} is not set; skipping PostgreSQL-specific test"
        )
    return url


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop the schemas and template databases the Postgres tests shared."""
    drop_session_objects()
