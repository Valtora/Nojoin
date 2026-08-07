"""Keep one instance of a job running at a time, across worker processes.

Some work is dispatched on a stream of triggers but only makes sense one at a
time. A Meeting Edge refresh is the case this was written for: it is queued as
the transcript grows, so during a live meeting a trigger arrives every few
seconds, while a single refresh takes 20-45 seconds. Nothing stopped four of
them running at once, each spawning its own LLM subprocess, all but the last
producing a result immediately superseded.

Redis rather than a database row because the guard has to survive the process
holding it being killed. A `SET NX EX` expires on its own, so a worker that dies
mid-job releases the job within the TTL instead of wedging the feature until
someone notices.

Fails open. A guard that cannot reach Redis lets the work run, because the
alternative is that a broker problem silently switches a feature off. In
practice a Redis outage stops the tasks being delivered at all, so this is about
being wrong in the harmless direction rather than a case that comes up.
"""

from __future__ import annotations

import logging
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


def _open_client():
    """The Redis client this module uses.

    A named seam rather than an inline call so tests can swap in an in-memory
    double and exercise the guard itself, instead of only ever reaching the
    fail-open path.
    """
    return redis.from_url(get_redis_url(), decode_responses=True)


@contextmanager
def single_flight(name: str, *, ttl_seconds: int) -> Iterator[bool]:
    """Yield True when this caller owns `name`, False when someone else does.

    The caller decides what a False means. For work driven by a stream of
    triggers the answer is usually to drop it: another trigger is moments away
    and will carry fresher input than the one being skipped.

    `ttl_seconds` bounds how long a crashed holder can block others, so it wants
    to be comfortably above the job's own timeout rather than tuned to its
    typical duration.
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

    try:
        yield True
    finally:
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


def _close(client) -> None:
    # redis.from_url builds a fresh connection pool per call, so a client that
    # is not closed leaks one for the life of the process.
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
