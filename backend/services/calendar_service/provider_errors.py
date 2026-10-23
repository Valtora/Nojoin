"""Reading a provider's error response for the log and the user.

Shared by connecting an account (the OAuth callback) and by push-channel
provisioning. Each helper reads only the error fields of a response body, never
the request, so no token, authorisation code, state value or calendar address
reaches the output, and any email address a provider echoes back is masked.

It imports only the ``models_dto`` leaf from the calendar package.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .models_dto import CalendarReauthorisationRequired

# Cap on the provider's error code and message kept in logs and ``last_error``.
PROVIDER_ERROR_DETAIL_LIMIT = 200
# A provider error code as it may travel in the settings redirect: Google's
# ``notACalendarUser``, Graph's ``MailboxNotEnabledForRESTAPI``, OAuth's
# ``invalid_client``. Anything else is dropped rather than echoed to the page.
PROVIDER_REASON_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
EMAIL_ADDRESS_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class CalendarConnectFailed(Exception):
    """A provider refused or failed a step of connecting an account.

    ``step`` names where it happened, ``reason`` is the provider's own error code
    when it gave a usable one, and ``detail`` is the described failure the
    callback logs.
    """

    def __init__(self, step: str, exc: Exception) -> None:
        self.step = step
        self.reason = _provider_error_reason(exc)
        self.detail = _describe_provider_failure(exc)
        super().__init__(f"{step}: {self.detail}")


def _truncate_error(value: str | None, limit: int = 500) -> str | None:
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:limit] if cleaned else None


def _google_error_reason(error: dict[str, Any]) -> str | None:
    """Google's specific reason (``pushWebhookBadDomain``), else its status."""
    for item in error.get("errors") or []:
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            return item["reason"]
    status = error.get("status")
    return status if isinstance(status, str) else None


def _provider_error_fields(payload: Any) -> tuple[str | None, str | None]:
    """Pick the error code and message out of a provider's JSON error body.

    Graph sends ``{"error": {"code": "InvalidRequest", "message": ...}}``, Google
    ``{"error": {"code": 400, "message": ..., "errors": [{"reason": ...}]}}``,
    and a token endpoint ``{"error": "invalid_grant", "error_description": ...}``.
    Google's numeric ``code`` repeats the HTTP status, so its reason stands in.
    """
    if not isinstance(payload, dict):
        return None, None
    error = payload.get("error")
    if isinstance(error, str):
        description = payload.get("error_description")
        return error, description if isinstance(description, str) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    if not isinstance(code, str):
        code = _google_error_reason(error)
    message = error.get("message")
    return code, message if isinstance(message, str) else None


def _response_error_fields(
    exc: httpx.HTTPStatusError,
) -> tuple[str | None, str | None]:
    try:
        payload = exc.response.json()
    except ValueError:
        payload = None
    return _provider_error_fields(payload)


def _mask_addresses(value: str) -> str:
    return EMAIL_ADDRESS_PATTERN.sub("<address>", value)


def _describe_provider_failure(exc: Exception) -> str:
    """Summarise a failed provider call for the log and ``last_error``.

    Keeps the HTTP status and the provider's own error code and message, which
    say why a request was refused where httpx's status line does not, and caps
    the provider's text. Any other exception keeps its type and message.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code, message = _response_error_fields(exc)
        detail = _truncate_error(
            _mask_addresses(": ".join(part for part in (code, message) if part)),
            PROVIDER_ERROR_DETAIL_LIMIT,
        )
        status_line = f"HTTP {exc.response.status_code}"
        return f"{status_line} {detail}" if detail else status_line
    detail = _truncate_error(_mask_addresses(str(exc)), PROVIDER_ERROR_DETAIL_LIMIT)
    name = type(exc).__name__
    return f"{name}: {detail}" if detail else name


def _describe_failure_for_log(exc: Exception) -> str:
    """Describe a failure at a boundary that catches everything.

    A provider's answer, a network error and a refused grant are described in
    full. Anything else keeps only its type: a database error's message carries
    the statement's parameters, which can hold calendar names and event text.
    """
    if isinstance(exc, (httpx.HTTPError, CalendarReauthorisationRequired)):
        return _describe_provider_failure(exc)
    return type(exc).__name__


def _usable_reason(value: str | None) -> str | None:
    if value and PROVIDER_REASON_PATTERN.fullmatch(value):
        return value
    return None


def _provider_error_reason(exc: Exception) -> str | None:
    """The provider's error code, when it is safe to carry to the settings page."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    code, _message = _response_error_fields(exc)
    return _usable_reason(code)
