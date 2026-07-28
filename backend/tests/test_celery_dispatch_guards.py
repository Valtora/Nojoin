"""The suite's guards against reaching a real Redis.

These encode a failure that cost the suite roughly nine minutes a run. A
transcripts fixture stubbed `_dispatch_meeting_edge_refresh` on the package
that re-exports it, but the route modules bind the name at import time, so the
stub patched an attribute nobody reads. The real dispatcher ran on every
mutating request, retried against an absent broker for 19s, and had its failure
swallowed by a best-effort `except Exception`. Every test still passed.

Nothing here asserts application behaviour. They assert that the same mistake
would now be loud rather than merely slow.
"""

import time

import pytest

from backend.api.v1.endpoints.transcripts.helpers import (
    _dispatch_meeting_edge_refresh,
)
from backend.celery_app import celery_app
from backend.tests.redis_guard import (
    RedisContactedInTests,
    transcripts_route_modules,
)


def test_dispatch_retry_policies_are_bounded():
    """A leaked dispatch must cost milliseconds, not seconds.

    The result backend is the expensive half and the one no top-level Celery
    setting reaches: `send_task` subscribes it to the new task's pubsub channel
    before publishing, and its own policy retries 20 times a second apart.

    Bounded rather than exactly zero, because building an app during a test
    applies the API's own limits (`apply_api_dispatch_limits`, one retry). The
    property that matters is that neither is the stock 20.
    """
    assert celery_app.backend.retry_policy["max_retries"] <= 1
    assert celery_app.conf.task_publish_retry_policy["max_retries"] <= 1
    assert celery_app.conf.task_publish_retry is False
    assert celery_app.conf.broker_connection_retry is False


@pytest.mark.anyio
async def test_a_swallowed_dispatch_is_still_recorded(redis_contact_guard):
    """The swallow hides the failure from the caller, not from the guard.

    This is the exact shape of the original bug: the dispatcher catches
    everything and returns normally, so no assertion in the test body could
    ever have noticed. The guard records at the connection layer instead.
    """
    started = time.perf_counter()
    await _dispatch_meeting_edge_refresh(1)
    elapsed = time.perf_counter() - started

    assert redis_contact_guard, "an unstubbed dispatch was not recorded"
    # Comfortably under the 19s the unbounded result-backend policy cost, and
    # far above the ~0.003s this actually takes.
    assert elapsed < 2.0, f"dispatch took {elapsed:.2f}s; retry policy unbounded?"

    redis_contact_guard.clear()


def test_guard_refuses_the_connection_rather_than_letting_it_retry(
    redis_contact_guard,
):
    """redis-py retries `ConnectionError` internally, so the guard raises
    something nobody retries."""
    assert not issubclass(RedisContactedInTests, ConnectionError)

    with pytest.raises(RedisContactedInTests):
        celery_app.send_task("backend.worker.tasks.refresh_meeting_edge_task", args=[1])

    assert redis_contact_guard
    redis_contact_guard.clear()


def test_meeting_edge_stub_covers_every_module_that_binds_the_dispatcher(
    stub_meeting_edge_dispatch,
):
    """Stubbing one module is what failed before; the fixture walks them all."""
    binding_modules = {
        module.__name__
        for module in transcripts_route_modules()
        if hasattr(module, "_dispatch_meeting_edge_refresh")
    }

    assert binding_modules, "no module binds the dispatcher; has it been renamed?"
    assert set(stub_meeting_edge_dispatch) == binding_modules
    # The routes, not just the package that re-exports it.
    assert any(name.endswith(".routes_segments") for name in binding_modules)
