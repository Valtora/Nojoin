"""Stall detection and its attribution.

The watchdog exists because a two-minute freeze during a live recording was
invisible to everything Nojoin logged. These tests hold the two halves it has to
get right: noticing the freeze at all, and only speaking when there is something
to say.
"""

import asyncio

import pytest

from backend.utils import stall_watchdog
from backend.utils.stall_watchdog import (
    StallWatchdog,
    _parse_pressure_file,
    peak_pressure,
    read_pressure,
)


class FakeClock:
    """A monotonic clock the test advances by hand, plus a sleep that lies.

    The point of the watchdog is measuring the gap between how long it asked to
    sleep and how long it actually slept, so a test needs to control both
    independently.
    """

    def __init__(self, *, actual_sleep_seconds):
        self.now = 1000.0
        self._actual = actual_sleep_seconds

    def monotonic(self):
        return self.now

    async def sleep(self, requested):
        self.now += self._actual(requested)


def build_watchdog(*, actual_sleep, pressure=None, **kwargs):
    clock = FakeClock(actual_sleep_seconds=actual_sleep)
    watchdog = StallWatchdog(
        interval_seconds=5.0,
        lag_threshold_seconds=1.0,
        pressure_threshold=20.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        pressure_reader=lambda: pressure,
        **kwargs,
    )
    return watchdog, clock


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def metrics_of_stage(caplog, stage):
    return [
        record
        for record in caplog.records
        if f'"stage":"{stage}"' in record.getMessage()
    ]


class TestPressureParsing:
    def test_extracts_avg10_per_scope(self):
        text = (
            "some avg10=12.34 avg60=1.00 avg300=0.10 total=99\n"
            "full avg10=5.60 avg60=0.50 avg300=0.05 total=42\n"
        )
        assert _parse_pressure_file(text) == {"some": 12.34, "full": 5.60}

    def test_tolerates_unparseable_values(self):
        # A malformed /proc read must not take the watchdog down with it.
        assert _parse_pressure_file("some avg10=notanumber total=1") == {}

    def test_ignores_blank_lines(self):
        assert _parse_pressure_file("\n\nsome avg10=1.00\n\n") == {"some": 1.0}

    def test_peak_is_the_worst_resource(self):
        assert peak_pressure({"cpu_some": 1.0, "memory_some": 44.2, "io_some": 3.0}) == 44.2

    def test_peak_of_missing_pressure_is_zero(self):
        # None means "the kernel does not tell us", which must never be read as
        # a healthy zero.
        assert peak_pressure(None) == 0.0

    def test_read_pressure_returns_none_without_psi(self, monkeypatch, tmp_path):
        monkeypatch.setattr(stall_watchdog, "PRESSURE_ROOT", tmp_path / "absent")
        assert read_pressure() is None


class TestLagDetection:
    @pytest.mark.anyio
    async def test_reports_a_stall_when_the_sleep_overshoots(self, caplog):
        # Asked for 5s, actually gone for 125s: the shape of the 6 August outage.
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested + 120.0,
            pressure={"memory_some": 91.0},
        )
        with caplog.at_level("INFO"):
            lag = await watchdog.tick()

        assert lag == pytest.approx(120.0)
        stalls = metrics_of_stage(caplog, "event_loop_stalled")
        assert len(stalls) == 1
        assert '"lag_s":120.0' in stalls[0].getMessage()

    @pytest.mark.anyio
    async def test_attributes_the_stall_with_kernel_pressure(self, caplog):
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested + 120.0,
            pressure={"memory_some": 91.0, "cpu_some": 2.0},
        )
        with caplog.at_level("INFO"):
            await watchdog.tick()

        message = metrics_of_stage(caplog, "event_loop_stalled")[0].getMessage()
        assert '"memory_some":91.0' in message
        assert '"cpu_some":2.0' in message

    @pytest.mark.anyio
    async def test_stays_silent_on_an_ordinary_overshoot(self, caplog):
        # A busy loop overshoots by milliseconds constantly. A watchdog that
        # reports that is one nobody reads.
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested + 0.05,
            pressure={"cpu_some": 0.0},
        )
        with caplog.at_level("INFO"):
            await watchdog.tick()

        assert metrics_of_stage(caplog, "event_loop_stalled") == []

    @pytest.mark.anyio
    async def test_reports_a_stall_even_without_psi(self, caplog):
        # Non-Linux hosts and PSI-less kernels still get the lag signal; they
        # just lose the attribution.
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested + 120.0,
            pressure=None,
        )
        with caplog.at_level("INFO"):
            await watchdog.tick()

        message = metrics_of_stage(caplog, "event_loop_stalled")[0].getMessage()
        assert "pressure_avg10" not in message


class TestPressureReporting:
    @pytest.mark.anyio
    async def test_reports_high_pressure_without_a_stall(self, caplog):
        # Early warning: the machine is degrading before it freezes outright.
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested,
            pressure={"memory_some": 55.0},
        )
        with caplog.at_level("INFO"):
            await watchdog.tick()

        assert len(metrics_of_stage(caplog, "host_pressure_high")) == 1

    @pytest.mark.anyio
    async def test_quiet_when_pressure_is_below_the_threshold(self, caplog):
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested,
            pressure={"memory_some": 1.0},
        )
        with caplog.at_level("INFO"):
            await watchdog.tick()

        assert metrics_of_stage(caplog, "host_pressure_high") == []

    @pytest.mark.anyio
    async def test_sustained_pressure_is_rate_limited(self, caplog):
        # Pressure that stays high would otherwise emit a line every tick and
        # bury the moment it started.
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested,
            pressure={"memory_some": 55.0},
        )
        with caplog.at_level("INFO"):
            for _ in range(5):
                await watchdog.tick()

        # 5 ticks of 5s spans 25s, inside the 60s report interval.
        assert len(metrics_of_stage(caplog, "host_pressure_high")) == 1

    @pytest.mark.anyio
    async def test_pressure_is_reported_again_after_the_interval(self, caplog):
        watchdog, _ = build_watchdog(
            actual_sleep=lambda requested: requested,
            pressure={"memory_some": 55.0},
        )
        with caplog.at_level("INFO"):
            for _ in range(30):
                await watchdog.tick()

        assert len(metrics_of_stage(caplog, "host_pressure_high")) > 1


class TestLifecycle:
    @pytest.mark.anyio
    async def test_a_failing_tick_does_not_kill_the_watchdog(self, caplog):
        calls = {"n": 0}

        async def exploding_sleep(_requested):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            if calls["n"] > 3:
                raise asyncio.CancelledError

        watchdog = StallWatchdog(
            interval_seconds=5.0,
            monotonic=lambda: 0.0,
            sleep=exploding_sleep,
            pressure_reader=lambda: None,
        )
        with pytest.raises(asyncio.CancelledError):
            await watchdog.run()

        assert calls["n"] > 1

    @pytest.mark.anyio
    async def test_stop_is_safe_when_never_started(self):
        watchdog = StallWatchdog(pressure_reader=lambda: None)
        await watchdog.stop()

    @pytest.mark.anyio
    async def test_start_then_stop_cancels_the_task(self):
        watchdog = StallWatchdog(
            interval_seconds=0.01,
            pressure_reader=lambda: None,
        )
        watchdog.start()
        await asyncio.sleep(0)
        await watchdog.stop()
        assert watchdog._task is None

    def test_disabled_by_environment(self, monkeypatch):
        monkeypatch.setenv(stall_watchdog.ENABLED_ENV, "false")
        assert stall_watchdog.is_enabled() is False
        assert stall_watchdog.start_stall_watchdog() is None

    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv(stall_watchdog.ENABLED_ENV, raising=False)
        assert stall_watchdog.is_enabled() is True

    def test_bad_env_values_fall_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv(stall_watchdog.INTERVAL_ENV, "not-a-number")
        monkeypatch.setenv(stall_watchdog.LAG_THRESHOLD_ENV, "-5")
        watchdog = StallWatchdog(pressure_reader=lambda: None)
        assert watchdog.interval_seconds == stall_watchdog.DEFAULT_INTERVAL_SECONDS
        assert (
            watchdog.lag_threshold_seconds
            == stall_watchdog.DEFAULT_LAG_THRESHOLD_SECONDS
        )
