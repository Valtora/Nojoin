"""OAuth redirect, webhook, and account-settings URL builders.

Kept as a dependency leaf so both ``config`` (provider status) and ``oauth``
(authorisation/token exchange) can build provider URLs without importing each
other.
"""

from __future__ import annotations

from urllib.parse import urlencode

from backend.models.calendar import CalendarProvider
from backend.utils.config_manager import get_trusted_web_origin

from .constants import ACCOUNT_REDIRECT_PATHS
from .provider_errors import _usable_reason


def _build_redirect_uri(provider: str) -> str:
    return f"{get_trusted_web_origin()}/api/v1/calendar/oauth/{provider}/callback"


def _build_push_notification_url(provider: str) -> str:
    return f"{get_trusted_web_origin()}/api/v1/calendar/webhooks/{provider}"


def _build_account_redirect(
    status_value: str, provider: str, reason: str | None = None
) -> str:
    """The settings page a calendar sign-in returns to, as a relative path.

    ``reason`` is the provider's error code for a failed connection. It is added
    only to an ``error`` redirect and only when it looks like a provider code, so
    the page never echoes free text from a query string or a response body.
    """
    provider_redirects = ACCOUNT_REDIRECT_PATHS.get(provider)
    if provider_redirects is None:
        provider_redirects = ACCOUNT_REDIRECT_PATHS[CalendarProvider.GOOGLE.value]
    path = provider_redirects.get(status_value, provider_redirects["error"])
    if path == provider_redirects["error"] and _usable_reason(reason):
        path = f"{path}&{urlencode({'reason': reason})}"
    return path
