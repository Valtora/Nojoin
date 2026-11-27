"""What a failed provider call means for the next one.

One classifier for every provider and the CLI subscriptions, used by Test
connection's probe (``model_probe``) and by the live calls' backoff
(``backend.processing.live_ai_backoff``). SDK errors share no base class, so it
reads what each carries: an HTTP status (``status_code``, an integer ``code``
or ``response.status_code``), a ``Retry-After`` header, the error body's type
or code, and Gemini's structured error details. A backend wraps the SDK error
in its own, raised inside the ``except``, so the chain is walked through
``__cause__`` and ``__context__``.

Nothing here reads an error's message text: a provider can change its wording,
and the text can carry anything.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

FailureKind = Literal[
    # Cannot clear by itself: the settings or the account must change.
    "auth",
    "unknown_model",
    "billing",
    # Clears once the provider's limit resets.
    "rate_limit",
    # Transient.
    "timeout",
    "unreachable",
    "unavailable",
    "refused",
    "bad_response",
    "error",
]

CANNOT_CLEAR: frozenset[str] = frozenset({"auth", "unknown_model", "billing"})


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    # Seconds the provider asked for before the next request, when it said.
    retry_after_s: float | None = None

    @property
    def clears_by_itself(self) -> bool:
        return self.kind not in CANNOT_CLEAR


def exception_chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def status_code(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _class_names(exc: BaseException) -> list[str]:
    return [cls.__name__ for cls in type(exc).__mro__]


def _error_body(exc: BaseException) -> dict:
    """The provider's error object: ``{"type": ..., "code": ...}`` or empty."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        return inner if isinstance(inner, dict) else body
    return {}


def _gemini_details(exc: BaseException) -> list[dict]:
    """google.rpc details on a google-genai error (ErrorInfo, RetryInfo)."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return []
    error = details.get("error")
    items = error.get("details") if isinstance(error, dict) else None
    return [item for item in items or [] if isinstance(item, dict)]


def _gemini_reason(exc: BaseException) -> str | None:
    for item in _gemini_details(exc):
        if str(item.get("@type", "")).endswith("google.rpc.ErrorInfo"):
            reason = item.get("reason")
            return str(reason) if reason else None
    return None


def _gemini_retry_delay(exc: BaseException) -> float | None:
    for item in _gemini_details(exc):
        if str(item.get("@type", "")).endswith("google.rpc.RetryInfo"):
            delay = str(item.get("retryDelay") or "")
            if delay.endswith("s"):
                try:
                    return max(float(delay[:-1]), 0.0)
                except ValueError:
                    return None
    return None


def _retry_after_header(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        millis = headers.get("retry-after-ms")
        if millis:
            return max(float(millis) / 1000, 0.0)
        value = headers.get("retry-after")
    except Exception:  # noqa: BLE001 -- boundary: a header mapping of an unexpected shape means no delay was given
        return None
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(when.timestamp() - time.time(), 0.0)


def _contract_errors() -> tuple[type[BaseException], ...]:
    """The errors a reply that breaks its call's JSON contract raises."""
    from backend.processing.llm_backends.base import JSON_CONTRACT_ERRORS
    from backend.utils.speaker_name_suggestions import SpeakerSuggestionContractError

    return (*JSON_CONTRACT_ERRORS, SpeakerSuggestionContractError)


def _cli_failure(link: BaseException) -> Failure | None:
    # Imported here: the CLI driver pulls in the subscription SDK plumbing,
    # which a hosted provider's caller does not need.
    try:
        from backend.processing.cli.manager import (
            CliCredentialUnavailableError,
            CliUsageLimitError,
        )
    except ImportError:  # pragma: no cover - the CLI path ships with the worker
        return None
    if isinstance(link, CliUsageLimitError):
        resets_at = link.resets_at
        retry_after = None
        if resets_at is not None:
            if resets_at.tzinfo is None:
                resets_at = resets_at.replace(tzinfo=timezone.utc)
            retry_after = max(
                (resets_at - datetime.now(timezone.utc)).total_seconds(), 0.0
            )
        return Failure("rate_limit", retry_after)
    if isinstance(link, CliCredentialUnavailableError):
        return Failure("auth")
    return None


def _http_failure(link: BaseException, status: int) -> Failure | None:
    body = _error_body(link)
    if status in (401, 403):
        return Failure("auth")
    if status == 404:
        return Failure("unknown_model")
    if status == 402 or body.get("type") == "billing_error":
        return Failure("billing")
    if status == 429:
        if "insufficient_quota" in (body.get("code"), body.get("type")):
            return Failure("billing")
        return Failure(
            "rate_limit", _retry_after_header(link) or _gemini_retry_delay(link)
        )
    if status in (400, 422):
        if _gemini_reason(link) == "API_KEY_INVALID":
            return Failure("auth")
        return Failure("refused")
    if status >= 500:
        return Failure("unavailable", _retry_after_header(link))
    return None


def classify_failure(exc: BaseException) -> Failure:
    """Sort a failed call by what to do about it: stop, wait, or try again."""
    for link in exception_chain(exc):
        cli = _cli_failure(link)
        if cli is not None:
            return cli
        if isinstance(link, _contract_errors()):
            return Failure("bad_response")
        names = _class_names(link)
        # urllib3's NewConnectionError subclasses ConnectTimeoutError, so a
        # refused connection would otherwise read as a timeout.
        if isinstance(link, ConnectionRefusedError) or "NewConnectionError" in names:
            return Failure("unreachable")
        if isinstance(link, TimeoutError) or any("Timeout" in name for name in names):
            return Failure("timeout")
        status = status_code(link)
        if status is not None:
            failure = _http_failure(link, status)
            if failure is not None:
                return failure
        if any(name in ("ConnectError", "ConnectionError") for name in names):
            return Failure("unreachable")
    return Failure("error")


__all__ = [
    "CANNOT_CLEAR",
    "Failure",
    "FailureKind",
    "classify_failure",
    "exception_chain",
    "status_code",
]
