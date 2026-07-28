# ADR-0007: Bounded, fail-fast task dispatch from the API

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** @Valtora

## Context

The API dispatches Celery work from `async def` request handlers, in 34 places.
`celery_app.send_task` is a blocking socket call, so under uvicorn it runs on
the event loop: whatever it waits for stalls that worker process's other
in-flight requests, not only the request that dispatched.

Nothing bounded that wait. Two Redis subsystems are involved and both retried
generously by default. `send_task` first subscribes the **result backend** to
the new task's pubsub channel, which retried 20 times about a second apart, and
then publishes on the **broker**, which retried again. How long one attempt
takes is the operating system's business: a broker that refuses connections
fails per attempt in microseconds, but one that is merely unreachable — a
partition, a hung container, a firewall dropping packets — never answers, and
each attempt waits out the kernel's TCP connect timeout, roughly two minutes at
the default `tcp_syn_retries` of 6.

Measured on this repository against a broker that refuses, one dispatch blocked
for 19.04s and a concurrent request issued 50ms later took 18.99s. Against a
broker that drops packets, the dispatching request had not returned after 70s
and no concurrent request completed at all. The theoretical worst case is 20
retries times a ~127s connect, or roughly 42 minutes of wedged event loop, for
one best-effort refresh that nobody is waiting on.

This was invisible in production because Redis is normally reachable, and
invisible in tests because every dispatcher swallows its own failure. It
surfaced only as a slow test suite (see the commit that added
`backend/tests/redis_guard.py`).

This affects the deployment contract: how the API behaves when a runtime
dependency is unavailable.

## Decision

We will treat an unreachable Redis as a fast, local failure in the API rather
than something to wait out, and we will keep the event loop free while it is
being detected.

1. **Bound every connect attempt, in both processes.** `broker_transport_options`
   and the top-level `redis_socket_connect_timeout` both get a 2s connect
   timeout, in `backend/celery_app.py`. This caps how long a single TCP
   handshake may take and does nothing to an established connection, so the
   worker's blocking queue reads are unaffected. Note the asymmetry that makes
   this easy to get wrong: the broker reads its transport options, while the
   result backend reads top-level `redis_*` keys and ignores everything in
   `result_backend_transport_options` except its retry policy.
2. **Bound the number of attempts in the API only**, through
   `apply_api_dispatch_limits()`, called by `create_app`. The API caps the
   publish retry policy and the result backend's retry policy at one retry.
   The worker keeps Celery's defaults.
3. **Dispatch the best-effort Meeting Edge refresh off the event loop**, via
   `run_in_threadpool` in `backend/api/v1/endpoints/transcripts/helpers.py`.
4. **Keep swallowing a failed best-effort refresh**, deliberately rather than
   incidentally. It is logged at warning level and the request succeeds.

The worker and the API are deliberately configured differently because they
want opposite things from an outage. A worker writing a result is on its own
thread with nothing waiting on it, so retrying hard is right: the alternative
is a finished job whose result is lost. An API handler is on the event loop, so
retrying hard is exactly wrong: it converts one unavailable dependency into a
stalled process. Every API dispatch either queues background work whose failure
the caller already tolerates, or returns a task id the client re-polls, so
failing fast costs at most a retry the client can make, never a result that
cannot be recovered.

## Consequences

A dispatch against a refused broker now costs 0.03s rather than 19.04s, and
against an unreachable one 6.03s rather than being unbounded. During the latter
the event loop stays free: 60 concurrent no-op requests kept a 1.0ms median and
a 0.00s worst case, where previously not one completed. The healthy path is
unchanged at 0.7ms.

Three residual limits are accepted rather than solved:

- The other 33 dispatch sites are bounded but still synchronous, so during a
  total Redis outage each stalls its own request for up to ~6s. They are not
  best-effort — their responses carry a task id — so they must wait for the
  dispatch regardless; only the event-loop occupancy is worth removing, and
  doing so is a mechanical follow-up rather than part of this decision.
- `run_in_threadpool` borrows an anyio worker thread for the duration. During a
  sustained outage a burst of transcript mutations can occupy the default pool
  of 40. The degradation is graceful — further dispatches queue for a thread
  while the loop stays responsive — but it is a bound worth knowing.
- A lost Meeting Edge refresh is not retried. The next transcript mutation or
  live segment recomputes it from scratch, so the cost is one stale panel until
  the next edit.

Contributors adding a dispatch to an `async def` handler should assume it can
block for seconds and treat the event loop accordingly. Operators see no new
configuration; the timeouts are not tunable by design, because they encode a
property of the deployment topology (Redis is a container away) rather than a
preference.

## Alternatives Considered

**Leave it alone and rely on Redis being up.** Rejected on the numbers. The
failure mode is not a slow request but a process that stops serving every
client for as long as the network problem lasts, and a brief partition is an
ordinary event rather than an exotic one.

**Bound the publish retry policy only.** This is the obvious reading of the
symptom and it does nothing: measured, `task_publish_retry=False` plus
`broker_connection_retry=False` left a dispatch at 19.01s, because `send_task`
reaches the result backend first and never gets to the publish.

**Configure the retry counts globally instead of per process.** Simpler, but it
would make the worker drop results during a blip to solve a problem the worker
does not have.

**Move every dispatch off the loop and skip the bounding.** This fixes the
head-of-line blocking but leaves an individual request able to hang for 42
minutes, and it would leak threads at exactly the rate the outage produces
requests. Bounding is what makes the offload safe, so bounding is the primary
decision and the offload is secondary.

**Fire and forget with `asyncio.create_task`.** Returns the request
immediately, but detaches the work from any request scope and grows an unbounded
set of pending tasks precisely when Redis is unavailable. Awaiting a bounded
threadpool call keeps the failure observable and the concurrency accounted for.
