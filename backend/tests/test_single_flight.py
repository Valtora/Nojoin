"""One run at a time, across worker processes.

Written for Meeting Edge, which is queued as a transcript grows and so receives
a trigger every few seconds while a run takes 20-45 seconds. Four concurrent
runs were observed on one recording, each spawning its own LLM subprocess.
"""

import pytest

from backend.core import single_flight as single_flight_module
from backend.core.single_flight import KEY_PREFIX, single_flight
from backend.tests.conftest import FakeSingleFlightRedis


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
