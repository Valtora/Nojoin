"""Keep one instance of a job running at a time, across worker processes.

Some work is dispatched on a stream of triggers but only makes sense one at a
time. A Meeting Edge refresh is the main case: it is queued as the transcript
grows, so during a live meeting a trigger arrives every few seconds, while a
single refresh takes 20-45 seconds. Unguarded, several run at once, each
spawning its own LLM subprocess, and all but the last produce a result that is
immediately superseded.

Redis rather than a database row because the guard has to survive the process
holding it being killed. A `SET NX EX` expires on its own, so a worker that dies
mid-job releases the job within the TTL instead of wedging the feature until
someone notices.

A holder renews its key from a thread while its work runs (#1010), so the TTL
only bounds how long a lost worker blocks the next run. It cannot be sized to
the work: a provider call can outlast its own timeout through SDK retries and
fallbacks, and some calls set no timeout at all.

Fails open. A guard that cannot reach Redis lets the work run, because the
alternative is that a broker problem silently switches a feature off. In
practice a Redis outage stops the tasks being delivered at all, so this is about
being wrong in the harmless direction rather than a case that comes up.
"""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from typing import Iterator

import redis

from backend.core.redis import get_redis_url

logger = logging.getLogger(__name__)

KEY_PREFIX = "nojoin:single-flight:"

# Releasing a lock we no longer hold would let two runs proceed, which is the
# one thing this is for. Compare the token first, in the same round trip.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


# Renew a key only while this token holds it: 1 renewed, 0 gone, -1 held by
# another token. Never sets a missing key, so a renewal that lands after the
# release cannot bring the key back.
_RENEW_SCRIPT = """
local current = redis.call('get', KEYS[1])
if current == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
if not current then
    return 0
end
return -1
"""

# A held key is renewed this many times per TTL, so two renewals in a row can
# fail before it lapses.
RENEWALS_PER_TTL = 3
# How long a release waits for a renewal already sent to Redis.
_RENEWER_STOP_WAIT_SECONDS = 5.0


def _open_client():
    """The Redis client this module uses.

    A named seam rather than an inline call so tests can swap in an in-memory
    double and exercise the guard itself, instead of only ever reaching the
    fail-open path.
    """
    return redis.from_url(get_redis_url(), decode_responses=True)


def _renew_interval(ttl_seconds: int) -> float:
    return ttl_seconds / RENEWALS_PER_TTL


class _Renewer:
    """Keeps a held key alive from a daemon thread until the holder stops it.

    The holder is waiting on its work, so the renewal needs a thread of its
    own; side_runs.claim_heartbeat does the same for a transcript's database
    claim. The thread is a daemon, so a worker that is killed takes it along
    and the key expires within its TTL. It never raises into the holder: a
    renewal that cannot reach Redis is logged once and tried again at the next
    interval, and the key lapses if Redis stays out of reach. Once the key has
    gone, or another token holds it, renewing stops.
    """

    def __init__(self, name: str, token: str, ttl_seconds: int) -> None:
        self._name = name
        self._key = f"{KEY_PREFIX}{name}"
        self._token = token
        self._ttl_seconds = int(ttl_seconds)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"single-flight:{name}", daemon=True
        )

    def start(self) -> None:
        try:
            self._thread.start()
        except RuntimeError as exc:
            # Out of threads: the work still runs, on the TTL alone.
            logger.warning(
                "Single-flight guard for %s is not renewed: %s", self._name, exc
            )

    def stop(self) -> None:
        """Stop renewing, waiting up to five seconds for a renewal in flight."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=_RENEWER_STOP_WAIT_SECONDS)

    def _run(self) -> None:
        unreachable_logged = False
        while not self._stop.wait(_renew_interval(self._ttl_seconds)):
            try:
                renewed = self._renew()
            except Exception as exc:  # noqa: BLE001 -- boundary: the holder's work goes on; the key lapses if Redis stays out of reach
                if not unreachable_logged:
                    logger.warning(
                        "Single-flight guard for %s could not be renewed, "
                        "trying again: %s",
                        self._name,
                        exc,
                    )
                    unreachable_logged = True
                continue
            if renewed != 1:
                logger.warning(
                    "Single-flight guard for %s stopped renewing: %s",
                    self._name,
                    "another run holds it" if renewed == -1 else "it had expired",
                )
                return

    def _renew(self) -> int:
        client = _open_client()
        try:
            return int(
                client.eval(_RENEW_SCRIPT, 1, self._key, self._token, self._ttl_seconds)
            )
        finally:
            _close(client)


@contextmanager
def single_flight(name: str, *, ttl_seconds: int) -> Iterator[bool]:
    """Yield True when this caller owns `name`, False when someone else does.

    The caller decides what a False means. For work driven by a stream of
    triggers the answer is usually to drop it: another trigger is moments away
    and will carry fresher input than the one being skipped.

    The key is renewed while the block runs, so `ttl_seconds` only bounds how
    long a crashed holder can block others, however long the work takes.
    """
    key = f"{KEY_PREFIX}{name}"
    token = uuid.uuid4().hex
    client = None
    acquired = False

    try:
        client = _open_client()
        acquired = bool(client.set(key, token, nx=True, ex=ttl_seconds))
    except Exception as exc:  # noqa: BLE001 -- boundary: the guard fails open
        logger.warning(
            "Single-flight guard for %s could not reach Redis, running anyway: %s",
            name,
            exc,
        )
        if client is not None:
            _close(client)
        yield True
        return

    if not acquired:
        _close(client)
        yield False
        return

    renewer = _Renewer(name, token, ttl_seconds)
    try:
        renewer.start()
        yield True
    finally:
        # Stopped first, so no renewal follows the release.
        renewer.stop()
        try:
            client.eval(_RELEASE_SCRIPT, 1, key, token)
        except Exception as exc:  # noqa: BLE001 -- boundary: the TTL is the backstop
            logger.warning(
                "Single-flight guard for %s could not be released, "
                "leaving it to expire: %s",
                name,
                exc,
            )
        finally:
            _close(client)


# What every claim stored before each got a token of its own (#1011). A task
# queued by that code carries no token and releases with this one, which can
# free only a claim the same code made.
LEGACY_CLAIM_TOKEN = "claimed"

# Hold a claim for the token that made it: renew its expiry while that token
# holds it, or take it back when it has expired and nobody has claimed it
# since. A claim held by any other token is left alone.
_HOLD_SCRIPT = """
local current = redis.call('get', KEYS[1])
if current == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
if not current then
    redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[2])
    return 1
end
return 0
"""


def claim(name: str, *, ttl_seconds: int) -> str | None:
    """Hold `name` until its token releases it or the TTL ends; None when held.

    For work that is queued, not run, one at a time: the dispatcher claims
    before queueing and hands the token to the task, which releases it. Only
    that token frees the claim, so a task that outlived its TTL cannot free a
    newer claim. Fails open, like the guard: without Redis the work is queued
    with a token that matches nothing.
    """
    token = uuid.uuid4().hex
    client = None
    try:
        client = _open_client()
        key = f"{KEY_PREFIX}{name}"
        if client.set(key, token, nx=True, ex=ttl_seconds):
            return token
        return None
    except Exception as exc:  # noqa: BLE001 -- boundary: the claim fails open
        logger.warning("Claim %s could not reach Redis, queueing anyway: %s", name, exc)
        return token
    finally:
        if client is not None:
            _close(client)


@contextmanager
def hold_claim(name: str, token: str, *, ttl_seconds: int) -> Iterator[bool]:
    """Hold `name` for `token` while the block runs; False when another holds it.

    A queued task enters this as it starts, so the time it waited in the queue
    does not count against its work. The claim is renewed for `ttl_seconds`,
    or taken back when it expired in the queue and nobody has claimed it since,
    then kept renewed while the block runs and released when it ends. A claim
    held by another token means newer work is queued, and this task's is
    surplus. Fails open.
    """
    client = None
    held: bool | None = None
    try:
        client = _open_client()
        held = bool(
            client.eval(_HOLD_SCRIPT, 1, f"{KEY_PREFIX}{name}", token, int(ttl_seconds))
        )
    except Exception as exc:  # noqa: BLE001 -- boundary: the claim fails open
        logger.warning("Claim %s could not reach Redis, running anyway: %s", name, exc)
    finally:
        if client is not None:
            _close(client)

    if held is None:
        yield True
        return
    if not held:
        yield False
        return

    renewer = _Renewer(name, token, ttl_seconds)
    try:
        renewer.start()
        yield True
    finally:
        renewer.stop()
        release_claim(name, token)


def release_claim(name: str, token: str) -> None:
    """Free `name` if `token` still holds it; the TTL is the backstop."""
    client = None
    try:
        client = _open_client()
        client.eval(_RELEASE_SCRIPT, 1, f"{KEY_PREFIX}{name}", token)
    except Exception as exc:  # noqa: BLE001 -- boundary: the TTL is the backstop
        logger.warning("Claim %s could not be released: %s", name, exc)
    finally:
        if client is not None:
            _close(client)


def _close(client) -> None:
    # redis.from_url builds a fresh connection pool per call, so a client that
    # is not closed leaks one for the life of the process.
    try:
        client.close()
    except Exception:  # noqa: BLE001 -- boundary: closing the client is best-effort teardown
        pass
