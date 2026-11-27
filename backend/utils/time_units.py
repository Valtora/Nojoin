"""Conversions between the units a time is written in.

Times cross module boundaries as integer milliseconds. The units below it stay
inside the code that owns them: audio samples inside audio handling, model
frames inside inference, float seconds where a library or the browser speaks
them. New and changed code converts through this module, so one rule decides
each direction; conversions still written in place elsewhere move over as they
are touched.

The rules, one per direction:

* To an integer from a sample count: the nearest value, an exact half going to
  the even one. Integer arithmetic does it, so a sample count that lasts a
  whole number of milliseconds converts to exactly that number.
* To an integer from float seconds: Python's ``round`` of the float product.
  That is the nearest value, but the float decides its own ties: 0.0005 s
  times 1000 is not exactly 0.5.
* Integer to float seconds: one division.
* Milliseconds to model frames: a frame range covers every frame the interval
  touches, so its start rounds down and its end rounds up.

An interval is half-open, ``[start, end)``. Converting a time between units
does not move it past the nearest unit; a caller that treats an instant or an
empty span specially says so itself. ``frame_range`` alone changes an
interval, by design: it covers every frame the interval touches and stops at
the first frame still held.

A float second time is only as exact as the float: ``0.1 + 0.2`` is
``0.30000000000000004``. Convert it once, at the boundary, and compare the
integers; a comparison made on float seconds can come out differently for the
same words depending on where in a meeting they fall.
"""

from __future__ import annotations

MS_PER_SECOND = 1000

# One row of a native speaker-posterior array: Nemotron's 160-sample hop at
# 16 kHz.
NATIVE_FRAME_MS = 10


def _divide_nearest_even(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    twice = 2 * remainder
    if twice > denominator or (twice == denominator and quotient % 2):
        return quotient + 1
    return quotient


def _require_rate(sample_rate_hz: int) -> None:
    if sample_rate_hz <= 0:
        raise ValueError(f"sample rate must be positive, got {sample_rate_hz} Hz")


def seconds_to_ms(seconds: float) -> int:
    """Float seconds to integer milliseconds, nearest (see the module note on ties)."""
    return round(seconds * MS_PER_SECOND)


def ms_to_seconds(milliseconds: int) -> float:
    """Integer milliseconds to float seconds."""
    return milliseconds / MS_PER_SECOND


def samples_to_ms(samples: int, sample_rate_hz: int) -> int:
    """The length of a sample count in whole milliseconds, nearest."""
    _require_rate(sample_rate_hz)
    return _divide_nearest_even(samples * MS_PER_SECOND, sample_rate_hz)


def seconds_to_samples(seconds: float, sample_rate_hz: int) -> int:
    """Float seconds to a sample count, nearest.

    ``samples_to_seconds`` and this are inverses for any sample count a
    recording can hold, which truncating the product is not.
    """
    _require_rate(sample_rate_hz)
    return round(seconds * sample_rate_hz)


def samples_to_seconds(samples: int, sample_rate_hz: int) -> float:
    """A sample count to float seconds."""
    _require_rate(sample_rate_hz)
    return samples / sample_rate_hz


def frame_range(
    start_ms: int, end_ms: int, frame_ms: int, first_frame: int = 0
) -> tuple[int, int]:
    """The frames ``[first, stop)`` that ``[start_ms, end_ms)`` touches.

    ``frame_ms`` is the length of a frame and ``first_frame`` the index of the
    first frame still held, so the range indexes the retained array. A frame
    the interval covers in part counts: the start floors and the end ceils.
    Integer arithmetic keeps a word that ends on a frame edge from reaching one
    frame further because of a float product.
    """
    return (
        max(0, start_ms // frame_ms - first_frame),
        max(0, -(-end_ms // frame_ms) - first_frame),
    )
