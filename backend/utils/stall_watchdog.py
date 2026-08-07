"""Detects and attributes process-wide stalls.

A live recording is the one workload where a two-minute stall is unrecoverable:
the browser keeps counting wall-clock time while nothing reaches the server, and
the audio for that stretch is simply gone. Nojoin had no signal for it. A stall
on 6 August 2026 took three ~120s outages to reconstruct from nginx access logs,
because the only evidence was requests that completed late.

Two signals, because one alone cannot say what happened:

- **Event-loop lag.** A task sleeps a fixed interval and measures the overshoot.
  Whatever froze the process -- a blocked event loop, cgroup CPU throttling, the
  host paging us out -- shows up here, because the sleep cannot return on time.
  This detects the stall from inside without needing to know its cause.
- **Pressure Stall Information.** The kernel's own account of how long tasks
  were blocked on cpu, io or memory, read at the moment lag is detected. This is
  what names the cause. PSI's avg10 window still reflects a stall that has just
  ended, so reading it after the fact is meaningful.

Nothing here is sampled on a schedule into the log. A quiet system stays quiet:
we log when lag exceeds a threshold, or when pressure is high enough to be worth
knowing about on its own.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.processing.pipeline_metrics import record_pipeline_metric

logger = logging.getLogger(__name__)

PRESSURE_ROOT = Path("/proc/pressure")
PRESSURE_RESOURCES = ("cpu", "io", "memory")

ENABLED_ENV = "NOJOIN_STALL_WATCHDOG_ENABLED"
INTERVAL_ENV = "NOJOIN_STALL_WATCHDOG_INTERVAL_SECONDS"
LAG_THRESHOLD_ENV = "NOJOIN_STALL_WATCHDOG_LAG_THRESHOLD_SECONDS"
PRESSURE_THRESHOLD_ENV = "NOJOIN_STALL_WATCHDOG_PRESSURE_THRESHOLD"

DEFAULT_INTERVAL_SECONDS = 5.0
# Generous on purpose. A busy event loop routinely overshoots by tens of
# milliseconds, and a watchdog that cries at that is one nobody reads.
DEFAULT_LAG_THRESHOLD_SECONDS = 1.0
# Percent of the last 10 seconds during which some task was blocked. Sustained
# double digits is already a degraded machine; the 6 August stall would have
# been three figures away from this.
DEFAULT_PRESSURE_THRESHOLD = 20.0

# Once pressure is high it tends to stay high, and a line every tick would bury
# the event that mattered. Report at most this often while it persists.
PRESSURE_REPORT_INTERVAL_SECONDS = 60.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric value for %s: %r", name, raw)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive value for %s: %r", name, raw)
        return default
    return value


def is_enabled() -> bool:
    raw = os.getenv(ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _parse_pressure_file(text: str) -> dict[str, float]:
    """Extract the avg10 figures from one /proc/pressure file.

    Format is one line per scope, e.g.
    ``some avg10=0.00 avg60=0.00 avg300=0.00 total=0``. Only avg10 is kept:
    the watchdog reports on stalls it has just seen, and the longer windows
    dilute exactly the spike being investigated.
    """
    values: dict[str, float] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        scope = fields[0]
        for field in fields[1:]:
            key, _, raw = field.partition("=")
            if key != "avg10":
                continue
            try:
                values[scope] = float(raw)
            except ValueError:
                pass
    return values


def read_pressure() -> dict[str, float] | None:
    """Current PSI avg10 figures, or None where the kernel does not expose them.

    Absent on non-Linux hosts and on kernels built without PSI, so every caller
    has to tolerate None rather than treat it as zero pressure.
    """
    if not PRESSURE_ROOT.is_dir():
        return None

    pressure: dict[str, float] = {}
    for resource in PRESSURE_RESOURCES:
        try:
            text = (PRESSURE_ROOT / resource).read_text(encoding="utf-8")
        except OSError:
            continue
        for scope, value in _parse_pressure_file(text).items():
            pressure[f"{resource}_{scope}"] = value

    return pressure or None


def peak_pressure(pressure: dict[str, float] | None) -> float:
    """Worst avg10 across every resource, for threshold comparisons."""
    if not pressure:
        return 0.0
    return max(pressure.values())


@dataclass(frozen=True)
class WatchdogEnvironment:
    """Everything the watchdog reads from outside itself.

    Grouped so a test can supply a clock that jumps, a sleep that lies about how
    long it took, and a fixed pressure reading, without stalling a test run for
    a real second to prove a real second was detected.
    """

    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    pressure_reader: Callable[[], dict[str, float] | None] = read_pressure


class StallWatchdog:
    """Reports event-loop stalls and host pressure on the loop it is started on."""

    def __init__(
        self,
        *,
        interval_seconds: float | None = None,
        lag_threshold_seconds: float | None = None,
        pressure_threshold: float | None = None,
        process: str = "api",
        environment: WatchdogEnvironment | None = None,
    ) -> None:
        environment = environment or WatchdogEnvironment()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else _env_float(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS)
        )
        self.lag_threshold_seconds = (
            lag_threshold_seconds
            if lag_threshold_seconds is not None
            else _env_float(LAG_THRESHOLD_ENV, DEFAULT_LAG_THRESHOLD_SECONDS)
        )
        self.pressure_threshold = (
            pressure_threshold
            if pressure_threshold is not None
            else _env_float(PRESSURE_THRESHOLD_ENV, DEFAULT_PRESSURE_THRESHOLD)
        )
        self.process = process
        self._monotonic = environment.monotonic
        self._sleep = environment.sleep
        self._pressure_reader = environment.pressure_reader
        self._task: asyncio.Task | None = None
        self._last_pressure_report_at: float | None = None

    async def tick(self) -> float:
        """One sleep-and-measure cycle. Returns the overshoot in seconds."""
        started = self._monotonic()
        await self._sleep(self.interval_seconds)
        lag = self._monotonic() - started - self.interval_seconds
        self._report(max(lag, 0.0))
        return lag

    def _report(self, lag_seconds: float) -> None:
        stalled = lag_seconds >= self.lag_threshold_seconds
        # Reading PSI costs three small file reads, so only do it when there is
        # something to say -- either a stall to attribute or a periodic check.
        pressure = self._pressure_reader() if stalled or self._pressure_due() else None
        peak = peak_pressure(pressure)

        if stalled:
            payload: dict[str, Any] = {
                "process": self.process,
                "lag_s": round(lag_seconds, 3),
                "interval_s": self.interval_seconds,
            }
            if pressure is not None:
                payload["pressure_avg10"] = {
                    key: round(value, 2) for key, value in sorted(pressure.items())
                }
            record_pipeline_metric(
                stage="event_loop_stalled",
                status="warning",
                payload=payload,
                log=logger,
            )
            self._last_pressure_report_at = self._monotonic()
            return

        if pressure is not None and peak >= self.pressure_threshold:
            record_pipeline_metric(
                stage="host_pressure_high",
                status="warning",
                payload={
                    "process": self.process,
                    "peak_avg10": round(peak, 2),
                    "threshold": self.pressure_threshold,
                    "pressure_avg10": {
                        key: round(value, 2) for key, value in sorted(pressure.items())
                    },
                },
                log=logger,
            )
            self._last_pressure_report_at = self._monotonic()

    def _pressure_due(self) -> bool:
        if self._last_pressure_report_at is None:
            return True
        elapsed = self._monotonic() - self._last_pressure_report_at
        return elapsed >= PRESSURE_REPORT_INTERVAL_SECONDS

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- a watchdog must outlive its own bugs
                logger.warning("Stall watchdog tick failed.", exc_info=True)
                await self._sleep(self.interval_seconds)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self.run(), name="nojoin-stall-watchdog")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def start_stall_watchdog(*, process: str = "api") -> StallWatchdog | None:
    """Start the watchdog on the running loop, unless switched off."""
    if not is_enabled():
        logger.info("Stall watchdog disabled by %s.", ENABLED_ENV)
        return None

    watchdog = StallWatchdog(process=process)
    watchdog.start()
    logger.info(
        "Stall watchdog running: sampling every %.1fs, reporting lag over %.1fs.",
        watchdog.interval_seconds,
        watchdog.lag_threshold_seconds,
    )
    return watchdog
