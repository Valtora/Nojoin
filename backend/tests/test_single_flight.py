"""One run at a time, across worker processes.

Written for Meeting Edge, which is queued as a transcript grows and so receives
a trigger every few seconds while a run takes 20-45 seconds. Four concurrent
runs were observed on one recording, each spawning its own LLM subprocess.
"""

import logging
import threading

import pytest
from billiard.exceptions import Terminated
from celery.exceptions import SoftTimeLimitExceeded, WorkerShutdown, WorkerTerminate

import backend.worker.tasks as tasks_module
from backend.core import single_flight as single_flight_module
from backend.core.single_flight import (
    KEY_PREFIX,
    LEGACY_CLAIM_TOKEN,
    claim,
    hold_claim,
    release_claim,
    single_flight,
)
from backend.tests.conftest import FakeSingleFlightRedis
from backend.tests.test_meeting_edge_toggle import (
    _live_edge_session,
    _run_live_edge_refresh,
)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeSingleFlightRedis()
    monkeypatch.setattr(single_flight_module, "_open_client", lambda: fake)
    return fake


class TestExclusion:
    def test_first_caller_acquires(self, fake_redis):
        with single_flight("job", ttl_seconds=60) as acquired:
            assert acquired is True

    def test_second_caller_is_refused_while_the_first_holds_it(self, fake_redis):
        with single_flight("job", ttl_seconds=60) as outer:
            assert outer is True
            with single_flight("job", ttl_seconds=60) as inner:
                assert inner is False

    def test_the_guard_is_released_on_exit(self, fake_redis):
        with single_flight("job", ttl_seconds=60):
            pass
        with single_flight("job", ttl_seconds=60) as acquired:
            assert acquired is True

    def test_the_guard_is_released_when_the_body_raises(self, fake_redis):
        # A refresh that throws must not lock the feature out until the TTL.
        with pytest.raises(RuntimeError):
            with single_flight("job", ttl_seconds=60):
                raise RuntimeError("boom")

        with single_flight("job", ttl_seconds=60) as acquired:
            assert acquired is True

    def test_different_names_do_not_exclude_each_other(self, fake_redis):
        # Two recordings must be able to refresh at the same time.
        with single_flight("meeting-edge:1", ttl_seconds=60) as first:
            with single_flight("meeting-edge:2", ttl_seconds=60) as second:
                assert first is True
                assert second is True


class TestKeying:
    def test_keys_are_namespaced(self, fake_redis):
        with single_flight("job", ttl_seconds=60):
            assert list(fake_redis.keys) == [f"{KEY_PREFIX}job"]

    def test_the_ttl_is_applied(self, fake_redis):
        # The TTL is the only thing that frees a job whose worker was killed.
        with single_flight("job", ttl_seconds=120):
            assert fake_redis.ttls[f"{KEY_PREFIX}job"] == 120


class TestRelease:
    def test_an_expired_guard_taken_by_someone_else_is_not_deleted(self, fake_redis):
        # The TTL lapses, another worker takes the job, then the original
        # finishes. Releasing here would put two runs in flight.
        key = f"{KEY_PREFIX}job"
        with single_flight("job", ttl_seconds=60):
            fake_redis.keys[key] = "someone-elses-token"

        assert fake_redis.keys[key] == "someone-elses-token"

    def test_the_client_is_closed(self, fake_redis):
        # from_url builds a fresh pool per call, so an unclosed client leaks one.
        with single_flight("job", ttl_seconds=60):
            pass
        assert fake_redis.closed == 1

    def test_the_client_is_closed_when_refused(self, fake_redis):
        with single_flight("job", ttl_seconds=60):
            with single_flight("job", ttl_seconds=60):
                pass
        assert fake_redis.closed == 2


class TestFailsOpen:
    def test_an_unreachable_redis_lets_the_work_run(self, monkeypatch, caplog):
        # A broker problem must not silently switch a feature off.
        def _explode():
            raise ConnectionError("no redis here")

        monkeypatch.setattr(single_flight_module, "_open_client", _explode)

        with caplog.at_level("WARNING"):
            with single_flight("job", ttl_seconds=60) as acquired:
                assert acquired is True

        assert "could not reach Redis" in caplog.text

    def test_a_failed_release_does_not_propagate(self, monkeypatch, fake_redis):
        # The TTL is the backstop; a release that cannot be sent is not the
        # caller's problem and must not turn a successful job into a failure.
        def _explode(*_args, **_kwargs):
            raise ConnectionError("dropped mid-job")

        with single_flight("job", ttl_seconds=60) as acquired:
            assert acquired is True
            monkeypatch.setattr(fake_redis, "eval", _explode)


class TestClaims:
    def test_a_release_after_a_newer_claim_leaves_the_newer_claim(self, fake_redis):
        # The first holder outlives its TTL and a newer claim takes the slot.
        # When the first finishes, its release must not free the newer one.
        key = f"{KEY_PREFIX}job"
        first = claim("job", ttl_seconds=60)
        del fake_redis.keys[key]
        newer = claim("job", ttl_seconds=60)

        release_claim("job", first)

        assert newer is not None and newer != first
        assert fake_redis.keys[key] == newer

    def test_a_held_claim_refuses_a_second(self, fake_redis):
        assert claim("job", ttl_seconds=60) is not None
        assert claim("job", ttl_seconds=60) is None

    def test_its_own_token_frees_a_claim(self, fake_redis):
        token = claim("job", ttl_seconds=60)
        release_claim("job", token)

        assert fake_redis.keys == {}

    def test_the_legacy_token_frees_only_a_legacy_claim(self, fake_redis):
        # A task queued before tokens carries none and releases with the old
        # constant, which a claim made by this code never holds.
        key = f"{KEY_PREFIX}job"
        token = claim("job", ttl_seconds=60)
        release_claim("job", LEGACY_CLAIM_TOKEN)
        assert fake_redis.keys[key] == token

        fake_redis.keys[key] = LEGACY_CLAIM_TOKEN
        release_claim("job", LEGACY_CLAIM_TOKEN)
        assert key not in fake_redis.keys

    def test_holding_a_claim_restarts_its_ttl_and_releases_it(self, fake_redis):
        key = f"{KEY_PREFIX}job"
        token = claim("job", ttl_seconds=300)

        with hold_claim("job", token, ttl_seconds=120) as held:
            assert held is True
            assert fake_redis.ttls[key] == 120

        assert key not in fake_redis.keys

    def test_holding_takes_back_a_claim_that_expired_unclaimed(self, fake_redis):
        key = f"{KEY_PREFIX}job"
        token = claim("job", ttl_seconds=60)
        del fake_redis.keys[key]

        with hold_claim("job", token, ttl_seconds=120) as held:
            assert held is True
            assert fake_redis.keys[key] == token

    def test_holding_leaves_a_newer_claim_alone(self, fake_redis):
        key = f"{KEY_PREFIX}job"
        first = claim("job", ttl_seconds=60)
        del fake_redis.keys[key]
        newer = claim("job", ttl_seconds=60)

        with hold_claim("job", first, ttl_seconds=120) as held:
            assert held is False

        assert fake_redis.keys[key] == newer
        assert fake_redis.ttls[key] == 60

    def test_a_claim_fails_open(self, monkeypatch):
        def _explode():
            raise ConnectionError("no redis here")

        monkeypatch.setattr(single_flight_module, "_open_client", _explode)

        assert claim("job", ttl_seconds=60) is not None
        with hold_claim("job", "token", ttl_seconds=60) as held:
            assert held is True
            assert _renewers("job") == []
        release_claim("job", "token")


def _renewers(name):
    return [t for t in threading.enumerate() if t.name == f"single-flight:{name}"]


def _wait_until(condition, timeout=2.0):
    event = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if condition():
            return True
        event.wait(0.01)
    return condition()


@pytest.mark.usefixtures("fast_renewal")
class TestRenewal:
    """A holder renews its key while its work runs, however long that is (#1010)."""

    def test_a_run_longer_than_its_ttl_keeps_the_guard(self, fake_redis):
        with single_flight("job", ttl_seconds=60):
            fake_redis.pass_time(600)

            with single_flight("job", ttl_seconds=60) as second:
                assert second is False

    def test_a_claim_held_longer_than_its_ttl_keeps_the_slot(self, fake_redis):
        token = claim("job", ttl_seconds=300)
        with hold_claim("job", token, ttl_seconds=120):
            fake_redis.pass_time(900)

            assert claim("job", ttl_seconds=300) is None

    def test_the_renewer_is_a_daemon(self, fake_redis):
        # A worker that is killed takes the thread with it, and the key expires.
        with single_flight("job", ttl_seconds=60):
            (renewer,) = _renewers("job")
            assert renewer.daemon is True

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("the provider failed"),
            SoftTimeLimitExceeded(),
            Terminated(15),
            WorkerShutdown(),
            WorkerTerminate(),
        ],
        ids=["error", "soft-time-limit", "revoked", "warm-shutdown", "cold-shutdown"],
    )
    def test_the_renewer_stops_on_every_exit(self, fake_redis, error):
        with single_flight("job", ttl_seconds=60):
            pass
        assert _renewers("job") == []

        with pytest.raises(type(error)):
            with single_flight("job", ttl_seconds=60):
                assert len(_renewers("job")) == 1
                raise error

        assert _renewers("job") == []
        assert fake_redis.keys == {}

    def test_a_claims_renewer_stops_when_its_block_raises(self, fake_redis):
        token = claim("job", ttl_seconds=300)
        with pytest.raises(RuntimeError):
            with hold_claim("job", token, ttl_seconds=120):
                assert len(_renewers("job")) == 1
                raise RuntimeError("the provider failed")

        assert _renewers("job") == []
        assert fake_redis.keys == {}

    def test_an_unreachable_redis_leaves_the_run_going_and_the_key_lapsing(
        self, fake_redis, monkeypatch, caplog
    ):
        attempts = []

        def unreachable():
            attempts.append(1)
            raise ConnectionError("no redis here")

        with caplog.at_level(logging.WARNING, logger=single_flight_module.__name__):
            with single_flight("job", ttl_seconds=60) as acquired:
                monkeypatch.setattr(single_flight_module, "_open_client", unreachable)
                assert _wait_until(lambda: len(attempts) >= 3)
                fake_redis.advance(60)
                monkeypatch.setattr(
                    single_flight_module, "_open_client", lambda: fake_redis
                )
                assert acquired is True
                assert _wait_until(lambda: _renewers("job") == [])

        assert fake_redis.keys == {}
        messages = [r.getMessage() for r in caplog.records]
        assert sum("could not be renewed" in m for m in messages) == 1
        assert sum("stopped renewing: it had expired" in m for m in messages) == 1

    def test_another_holder_stops_the_renewer_once(self, fake_redis, caplog):
        key = f"{KEY_PREFIX}job"
        with caplog.at_level(logging.WARNING, logger=single_flight_module.__name__):
            with single_flight("job", ttl_seconds=60):
                fake_redis.keys[key] = "someone-elses-token"
                fake_redis.ttls[key] = 60
                assert _wait_until(lambda: _renewers("job") == [])
                fake_redis.advance(30)

        assert fake_redis.keys[key] == "someone-elses-token"
        assert fake_redis.ttls[key] == 30
        messages = [r.getMessage() for r in caplog.records]
        assert (
            messages.count(
                "Single-flight guard for job stopped renewing: another run holds it"
            )
            == 1
        )


@pytest.mark.usefixtures("fast_renewal")
def test_a_meeting_edge_refresh_longer_than_its_ttl_keeps_out_a_second(
    monkeypatch, fake_single_flight
):
    """A primary that times out and a fallback that answers outlast the TTL."""
    session = _live_edge_session()
    calls = []

    class SlowChain:
        def generate_meeting_edge(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                fake_single_flight.pass_time(
                    3 * tasks_module.MEETING_EDGE_SINGLE_FLIGHT_TTL_SECONDS
                )
                # The next trigger arrives while this refresh is still waiting.
                tasks_module.refresh_meeting_edge_task.run(1)
            return "Guidance"

    _run_live_edge_refresh(monkeypatch, session, SlowChain())

    assert len(calls) == 1
