"""Back off a failing AI provider during a live capture (#994).

A live AI call (Meeting Edge, and the live speaker naming ask) is triggered
every few seconds while a meeting runs. Without this, a provider that fails on
every call is called on every trigger, with its fallback after it: a 53 minute
capture made about 1,500 failed calls to each. Here each provider in the chain
(a tier) fails into a backoff of its own:

- a failure that clears by itself waits 30 s, doubling each time to 5 minutes,
  and a rate limit waits at least as long as the provider asked;
- a failure that cannot (a refused key, an unknown model, no credit) stops the
  tier for the rest of the capture, until the AI settings change;
- a tier that answers starts again from nothing.

Inside a tier's window the chain goes straight to the next tier, and a call
with every tier waiting is skipped before the feature records anything. Callers
run the chain inside their single-flight key, so the decision and the call are
one at a time per recording and feature.

Both features share a recording's tier states, since they call the same chain:
a tier failing for one is skipped by the other. A state lists the features
(purposes) it has held up, each added the first time that feature fails on the
tier or passes it over, so its notice names every one of them. A failure reads
the tier's state again before writing it, as the other feature may have
written it during the call.

State is a Redis hash per recording, one field per tier, with a 12 hour TTL. A
tier is named by its role in the chain, its provider and its model: nothing
derived from a key or an address is stored or logged. A global epoch counter,
bumped when the AI settings are saved, when a subscription is connected or
disconnected, and when the io worker starts (keys live in ``.env``, so changing
one means a restart), retires every earlier state at once.

Everything fails open: without Redis a live call runs as it did before this,
with no backoff and no notice, and nothing raises into the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import redis
from celery.signals import worker_ready

from backend.core.redis import get_redis_url
from backend.processing.llm_backends.base import LLMBackend, live_call_backend
from backend.processing.llm_backends.failures import Failure, classify_failure
from backend.processing.llm_backends.model_probe import safe_provider_detail
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.config_manager import INSTALL_WIDE_AI_SETTING_KEYS
from backend.utils.llm_config import CLI_MODEL_FIELDS_BY_PROVIDER, SYSTEM_LLM_FIELDS

logger = logging.getLogger(__name__)

KEY_PREFIX = "nojoin:live-ai:"
EPOCH_KEY = f"{KEY_PREFIX}epoch"
STATE_TTL_SECONDS = 12 * 60 * 60
FIRST_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 5 * 60

PURPOSE_MEETING_EDGE = "meeting_edge"
PURPOSE_LIVE_NAMING = "live_naming"
# How a notice names each feature, in this order: the interface calls the name
# ask's setting "Suggest speaker names during a meeting".
PURPOSE_LABELS = {
    PURPOSE_MEETING_EDGE: "Meeting Edge",
    PURPOSE_LIVE_NAMING: "speaker name suggestions",
}
_PURPOSE_ORDER = tuple(PURPOSE_LABELS)
_LOG_NAMES = {
    PURPOSE_MEETING_EDGE: "Meeting Edge",
    PURPOSE_LIVE_NAMING: "Live naming ask",
}
_PAUSED_LEADS = {
    PURPOSE_MEETING_EDGE: "Meeting Edge is paused",
    PURPOSE_LIVE_NAMING: "Speaker name suggestions are paused",
}
_SKIP_METRIC_STAGES = {
    PURPOSE_MEETING_EDGE: "meeting_edge_refresh_skipped",
    PURPOSE_LIVE_NAMING: "live_naming_ask_skipped",
}

ROLE_SUBSCRIPTION = "subscription"
ROLE_PRIMARY = "primary"
ROLE_FALLBACK = "fallback"

GENERIC_FAILURE_MESSAGE = "Meeting Edge could not be updated."


def _open_client():
    """The Redis client; a named seam so tests can use an in-memory double."""
    return redis.from_url(get_redis_url(), decode_responses=True)


def _now() -> float:
    return time.time()


def _close(client: Any) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001 -- boundary: closing the client is best-effort teardown
        pass


def _state_key(recording_id: int) -> str:
    return f"{KEY_PREFIX}{recording_id}"


def backoff_seconds(failures: int) -> float:
    """The wait after the ``failures``-th failure in a row: 30 s doubling to 5 min."""
    return float(
        min(FIRST_BACKOFF_SECONDS * 2 ** max(failures - 1, 0), MAX_BACKOFF_SECONDS)
    )


# -- the tiers of a chain ---------------------------------------------------


@dataclass(frozen=True)
class Tier:
    backend: Any
    role: str
    provider_id: str
    label: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.role}|{self.provider_id}|{self.model}"


def chain_tiers(backend: Any) -> list[Tier]:
    """Each provider ``backend`` tries, in order, with its role in the chain.

    A subscription tier is the user's own; the first other tier is the
    install's primary and any after it its fallback.
    """
    leaves = backend.tiers() if isinstance(backend, LLMBackend) else (backend,)
    tiers: list[Tier] = []
    servers = 0
    for leaf in leaves:
        provider_id = str(getattr(leaf, "provider_id", "") or "")
        if provider_id == "cli":
            role = ROLE_SUBSCRIPTION
        else:
            role = ROLE_PRIMARY if servers == 0 else ROLE_FALLBACK
            servers += 1
        tiers.append(
            Tier(
                backend=leaf,
                role=role,
                provider_id=provider_id,
                label=str(getattr(leaf, "provider_label", "") or "The AI provider"),
                model=str(getattr(leaf, "model", "") or "its model"),
            )
        )
    return tiers


# -- the stored state -------------------------------------------------------


@dataclass(frozen=True)
class TierState:
    role: str
    provider_id: str
    label: str
    model: str
    kind: str
    failures: int
    stopped: bool
    until: float | None
    epoch: int
    purposes: tuple[str, ...] = field(default_factory=tuple)

    def waiting(self, now: float) -> bool:
        return self.stopped or (self.until is not None and now < self.until)

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "purposes": list(self.purposes)})

    @classmethod
    def from_json(cls, raw: str) -> TierState | None:
        try:
            data = json.loads(raw)
            return cls(
                role=str(data["role"]),
                provider_id=str(data["provider_id"]),
                label=str(data["label"]),
                model=str(data["model"]),
                kind=str(data["kind"]),
                failures=int(data["failures"]),
                stopped=bool(data["stopped"]),
                until=None if data.get("until") is None else float(data["until"]),
                epoch=int(data["epoch"]),
                purposes=tuple(str(p) for p in data.get("purposes") or ()),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _read_epoch(client: Any) -> int:
    value = client.get(EPOCH_KEY)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class BackoffStore:
    """One recording's tier states in Redis. Every method fails open."""

    def __init__(self, recording_id: int) -> None:
        self.recording_id = recording_id
        self.epoch = 0
        self.available = True

    def load(self) -> dict[str, TierState]:
        client = None
        try:
            client = _open_client()
            self.epoch = _read_epoch(client)
            raw = client.hgetall(_state_key(self.recording_id)) or {}
        except Exception as exc:  # noqa: BLE001 -- boundary: without Redis the call runs as before, with no backoff
            self.available = False
            logger.warning(
                "Live AI backoff for recording %s could not reach Redis, "
                "calling without it: %s",
                self.recording_id,
                exc,
            )
            return {}
        finally:
            if client is not None:
                _close(client)
        states: dict[str, TierState] = {}
        for key, value in raw.items():
            state = TierState.from_json(value)
            # A state from an earlier epoch was set before the settings changed.
            if state is not None and state.epoch == self.epoch:
                states[key] = state
        return states

    def read(self, key: str, fallback: TierState | None) -> TierState | None:
        """One tier's state as stored now, or ``fallback`` without Redis.

        The other live feature may have written it since ``load``.
        """
        if not self.available:
            return fallback
        client = None
        try:
            client = _open_client()
            raw = client.hget(_state_key(self.recording_id), key)
        except Exception as exc:  # noqa: BLE001 -- boundary: without Redis the state loaded at the start stands
            self.available = False
            logger.warning(
                "Live AI backoff for recording %s could not be read: %s",
                self.recording_id,
                exc,
            )
            return fallback
        finally:
            if client is not None:
                _close(client)
        state = TierState.from_json(raw) if raw else None
        return state if state is not None and state.epoch == self.epoch else None

    def save(self, key: str, state: TierState) -> None:
        self._write(lambda client, name: client.hset(name, key, state.to_json()))

    def clear(self, key: str) -> None:
        self._write(lambda client, name: client.hdel(name, key))

    def _write(self, write: Any) -> None:
        if not self.available:
            return
        client = None
        try:
            client = _open_client()
            name = _state_key(self.recording_id)
            write(client, name)
            client.expire(name, STATE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 -- boundary: a lost write only loses the backoff, never the call
            self.available = False
            logger.warning(
                "Live AI backoff for recording %s could not be saved: %s",
                self.recording_id,
                exc,
            )
        finally:
            if client is not None:
                _close(client)


# Settings that change which provider or model a live call reaches.
AI_SETTING_KEYS = frozenset(
    {
        *SYSTEM_LLM_FIELDS,
        *INSTALL_WIDE_AI_SETTING_KEYS,
        *(name for pair in CLI_MODEL_FIELDS_BY_PROVIDER.values() for name in pair),
        "usage_model",
        "cli_provider",
    }
)


def touches_ai_settings(keys: Any) -> bool:
    return any(key in AI_SETTING_KEYS for key in keys)


def bump_epoch() -> None:
    """Retire every tier state: the AI settings, a subscription or a key changed."""
    client = None
    try:
        client = _open_client()
        client.incr(EPOCH_KEY)
    except Exception as exc:  # noqa: BLE001 -- boundary: a missed bump leaves a stop in place until its 12 h TTL
        logger.warning("Live AI backoff epoch could not be advanced: %s", exc)
    finally:
        if client is not None:
            _close(client)


@worker_ready.connect
def bump_epoch_on_io_start(sender: Any, **kwargs: Any) -> None:
    """A restarted io worker may have new keys from ``.env``: start afresh."""
    from backend.celery_app import IO_QUEUE
    from backend.worker.tasks.pipeline import _sweeps_recordings

    if _sweeps_recordings(sender, IO_QUEUE):
        bump_epoch()


# -- the wording ------------------------------------------------------------


def _purpose_rank(purpose: str) -> tuple[int, str]:
    known = purpose in _PURPOSE_ORDER
    return (_PURPOSE_ORDER.index(purpose) if known else len(_PURPOSE_ORDER), purpose)


def _with_purpose(purposes: tuple[str, ...], purpose: str) -> tuple[str, ...]:
    return tuple(sorted({*purposes, purpose}, key=_purpose_rank))


def _features(purposes: tuple[str, ...] | list[str]) -> str:
    """The features a notice names, Meeting Edge first: "Meeting Edge and ..."."""
    labels = [
        PURPOSE_LABELS.get(p, p) for p in sorted(purposes, key=_purpose_rank)
    ] or ["Meeting Edge"]
    text = " and ".join(labels)
    return text[:1].upper() + text[1:]


# Why a tier failed, by its classified kind: the only source of the wording.
_REASONS = {
    "auth": "{provider} refused the API key for {model}",
    "unknown_model": "{provider} does not serve {model}",
    "billing": "{provider} reports no credit or quota left for {model}",
    "rate_limit": "{provider} is rate limiting {model}",
    "timeout": "{model} on {provider} did not answer in time",
    "unreachable": "{provider} could not be reached for {model}",
    "unavailable": "{provider} returned a server error for {model}",
    "refused": "{provider} refused the request for {model}",
    "bad_response": "{model} on {provider} answered in a form Nojoin could not read",
    "error": "{provider} failed for {model}",
}
# A user's own subscription, which has no API key and no model to fix.
_SUBSCRIPTION_REASONS = {
    "auth": "your {provider} needs reconnecting",
    "rate_limit": "your {provider} has reached its usage limit",
}


# Ollama takes no API key and serves only the models pulled to it.
_OLLAMA_REASONS = {
    "auth": "Ollama refused access to {model}",
    "unknown_model": "{model} is not on the Ollama server",
}


def failure_reason(state: TierState) -> str:
    """Why the tier failed, in plain words, from its classified kind only."""
    if state.role == ROLE_SUBSCRIPTION and state.kind in _SUBSCRIPTION_REASONS:
        template = _SUBSCRIPTION_REASONS[state.kind]
    elif state.provider_id == "ollama" and state.kind in _OLLAMA_REASONS:
        template = _OLLAMA_REASONS[state.kind]
    else:
        template = _REASONS.get(state.kind, _REASONS["error"])
    return template.format(provider=state.label, model=state.model)


def _wait_phrase(seconds: float) -> str:
    if seconds < 5:
        return "in a moment"
    if seconds < 90:
        return f"in {round(seconds / 5) * 5:.0f} seconds"
    minutes = round(seconds / 60)
    return f"in {minutes} minute{'s' if minutes != 1 else ''}"


def paused_message(purpose: str, states: list[TierState]) -> str:
    """The Meeting Edge panel's line while no tier answers. The same for anyone.

    The name ask has no panel; its message is only the exception's.
    """
    feature = _features((purpose,))
    if not states:
        return f"{feature} could not be updated."
    reasons = "; ".join(failure_reason(state) for state in states)
    if all(state.stopped for state in states):
        action = "It stays off until the AI settings change."
    else:
        action = "Nojoin will keep trying."
    lead = _PAUSED_LEADS.get(purpose, f"{feature} is paused")
    return f"{lead}: {reasons}. {action}"


def notice_message(state: TierState, *, fixable_by: str, now: float) -> str:
    """A tier's notice for the person viewing the capture."""
    lead = f"{_features(state.purposes)}: {failure_reason(state)}."
    if not state.stopped:
        wait = max((state.until or now) - now, 0.0)
        return f"{lead} Nojoin will try it again {_wait_phrase(wait)}."
    if fixable_by == "admin":
        return (
            f"{lead} Nojoin has stopped using it. Ask your administrator to "
            "check the AI provider settings."
        )
    if state.role == ROLE_SUBSCRIPTION:
        return f"{lead} Nojoin has stopped using it until you reconnect it."
    return f"{lead} Nojoin has stopped using it until the AI settings change."


# -- the chain --------------------------------------------------------------


class LiveAIUnavailable(RuntimeError):
    """No tier answered: each failed or is waiting. The message is the panel's."""

    def __init__(self, purpose: str, states: list[TierState]) -> None:
        self.purpose = purpose
        self.states = states
        super().__init__(paused_message(purpose, states))


def live_ai_error_message(exc: BaseException) -> str:
    """What a failed live run stores for the interface: never a provider's text."""
    if isinstance(exc, LiveAIUnavailable):
        return str(exc)
    return GENERIC_FAILURE_MESSAGE


def log_failed_run(
    log: logging.Logger, what: str, recording_id: int, exc: BaseException
) -> None:
    """Log a failed live run once.

    A run whose providers all failed was logged where each failed, one line
    per provider; anything else is unexpected and gets its traceback.
    """
    if isinstance(exc, LiveAIUnavailable):
        return
    log.error("%s failed for recording %s: %s", what, recording_id, exc, exc_info=True)


class LiveAIChain:
    """A backend's providers, each called only outside its backoff window."""

    def __init__(self, backend: Any, *, recording_id: int, purpose: str) -> None:
        self.recording_id = recording_id
        self.purpose = purpose
        self._tiers = chain_tiers(live_call_backend(backend))
        self._store = BackoffStore(recording_id)
        self._states = self._store.load()

    def waiting(self) -> bool:
        """Whether every tier is inside its window, so a call would call no one."""
        now = _now()
        return bool(self._tiers) and all(
            (state := self._states.get(tier.key)) is not None and state.waiting(now)
            for tier in self._tiers
        )

    def paused_message(self) -> str:
        """Why no tier can answer now, for the Meeting Edge panel."""
        return paused_message(self.purpose, self._tier_states())

    def skip_if_waiting(self, log: logging.Logger | None = None) -> bool:
        """Record the skip as a metric, not an error, when every tier waits.

        Each tier then names this feature too, so its notice says what the
        wait holds up, though the wait began with the other feature.
        """
        if not self.waiting():
            return False
        for tier in self._tiers:
            self._serves(tier)
        now = _now()
        record_pipeline_metric(
            stage=_SKIP_METRIC_STAGES.get(self.purpose, f"{self.purpose}_skipped"),
            recording_id=self.recording_id,
            payload={
                "reason": "backoff",
                "tiers": [
                    {
                        "role": state.role,
                        "provider": state.provider_id,
                        "kind": state.kind,
                        "retry_in_s": (
                            None
                            if state.stopped or state.until is None
                            else round(max(state.until - now, 0.0))
                        ),
                    }
                    for tier in self._tiers
                    if (state := self._states.get(tier.key)) is not None
                ],
            },
            log=log or logger,
        )
        return True

    def generate_meeting_edge(self, request: Any, *, timeout: int) -> Any:
        return self._call("generate_meeting_edge", request, timeout=timeout)

    def infer_speaker_suggestions(self, transcript: str, **kwargs: Any) -> Any:
        return self._call("infer_speaker_suggestions", transcript, **kwargs)

    def _tier_states(self) -> list[TierState]:
        return [
            state
            for tier in self._tiers
            if (state := self._states.get(tier.key)) is not None
        ]

    def _serves(self, tier: Tier) -> None:
        """Add this feature to a waiting tier's state, once: it is held up too."""
        state = self._states.get(tier.key)
        if state is None or self.purpose in state.purposes:
            return
        current = self._store.read(tier.key, state)
        if current is None:
            # It answered for the other feature since this chain loaded.
            return
        if self.purpose not in current.purposes:
            current = replace(
                current, purposes=_with_purpose(current.purposes, self.purpose)
            )
            self._store.save(tier.key, current)
        self._states[tier.key] = current

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        for index, tier in enumerate(self._tiers):
            state = self._states.get(tier.key)
            # The clock is read for each tier and again on a failure: a tier
            # before this one may have spent its whole timeout failing.
            if state is not None and state.waiting(_now()):
                self._serves(tier)
                continue
            try:
                result = getattr(tier.backend, method)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- boundary: every provider failure is classified and the next tier tried
                self._failed(tier, state, exc, _now())
                continue
            # This tier answers, so a later one's wait no longer means anything
            # and would outlive its use. A later tier that is stopped stays so.
            for answered in (tier, *self._tiers[index + 1 :]):
                done = self._states.get(answered.key)
                if done is not None and (answered is tier or not done.stopped):
                    self._states.pop(answered.key, None)
                    self._store.clear(answered.key)
            return result
        raise LiveAIUnavailable(self.purpose, self._tier_states())

    def _failed(
        self, tier: Tier, state: TierState | None, exc: Exception, now: float
    ) -> None:
        failure: Failure = classify_failure(exc)
        # The other feature may have failed on this tier, or seen it answer,
        # while this call ran: count on from what is stored now.
        state = self._store.read(tier.key, state)
        failures = (state.failures if state is not None else 0) + 1
        stopped = not failure.clears_by_itself
        until = None
        if not stopped:
            wait = backoff_seconds(failures)
            if failure.retry_after_s is not None:
                wait = max(wait, failure.retry_after_s)
            until = now + wait
        purposes = _with_purpose(
            state.purposes if state is not None else (), self.purpose
        )
        new_state = TierState(
            role=tier.role,
            provider_id=tier.provider_id,
            label=tier.label,
            model=tier.model,
            kind=failure.kind,
            failures=failures,
            stopped=stopped,
            until=until,
            epoch=self._store.epoch,
            purposes=purposes,
        )
        self._states[tier.key] = new_state
        self._store.save(tier.key, new_state)
        secret = getattr(tier.backend, "api_key", None)
        detail = safe_provider_detail(exc, [secret] if isinstance(secret, str) else ())
        logger.warning(
            "%s for recording %s: %s %s (%s) failed (%s), %s: %s",
            _LOG_NAMES.get(self.purpose, self.purpose),
            self.recording_id,
            tier.label,
            tier.model,
            tier.role,
            failure.kind,
            "stopped until the AI settings change"
            if stopped
            else f"trying it again in {round(until - now)} s",
            detail,
        )


def live_ai_chain(
    backend: Any, recording_id: int, *, purpose: str = PURPOSE_MEETING_EDGE
) -> LiveAIChain:
    """A live feature's chain for ``recording_id``, with each tier's backoff."""
    return LiveAIChain(backend, recording_id=recording_id, purpose=purpose)


# -- what the interface reads ----------------------------------------------


def notice_id(key: str, state: TierState) -> str:
    """Stable for one tier's kind of failure within one epoch.

    A feature joining the notice gives it a new id, so it is shown again with
    every feature it names.
    """
    raw = f"{key}|{state.kind}|{state.epoch}|{','.join(state.purposes)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def live_ai_notices(recording_id: int, *, viewer_is_admin: bool) -> list[dict]:
    """One notice per tier that is waiting or stopped, worded for the viewer.

    Fails open to no notices. A tier that answers again has no state, so its
    notice is gone from the next read.
    """
    store = BackoffStore(recording_id)
    states = store.load()
    now = _now()
    notices = []
    for key, state in sorted(states.items()):
        own = state.role == ROLE_SUBSCRIPTION
        fixable_by = "you" if own or viewer_is_admin else "admin"
        notices.append(
            {
                "id": notice_id(key, state),
                "purposes": list(state.purposes),
                "provider": state.label,
                "model": state.model,
                "kind": state.kind,
                "action": "stopped" if state.stopped else "retrying",
                "retry_at": (
                    None
                    if state.stopped or state.until is None
                    else datetime.fromtimestamp(state.until, UTC)
                ),
                "fixable_by": fixable_by,
                "settings": (
                    None
                    if fixable_by != "you"
                    else ("your-ai" if own else "ai-providers")
                ),
                "message": notice_message(state, fixable_by=fixable_by, now=now),
            }
        )
    return notices


__all__ = [
    "PURPOSE_LIVE_NAMING",
    "PURPOSE_MEETING_EDGE",
    "BackoffStore",
    "LiveAIChain",
    "LiveAIUnavailable",
    "Tier",
    "TierState",
    "backoff_seconds",
    "bump_epoch",
    "chain_tiers",
    "live_ai_chain",
    "live_ai_error_message",
    "live_ai_notices",
    "log_failed_run",
    "touches_ai_settings",
    "paused_message",
]
