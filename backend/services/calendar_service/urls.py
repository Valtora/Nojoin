"""OAuth redirect, webhook, and account-settings URL builders.

Kept as a dependency leaf so both ``config`` (provider status) and ``oauth``
(authorisation/token exchange) can build provider URLs without importing each
other.
"""

from __future__ import annotations

from backend.models.calendar import CalendarProvider
from backend.utils.config_manager import get_trusted_web_origin

from .constants import ACCOUNT_REDIRECT_PATHS


def _build_redirect_uri(provider: str) -> str:
    return f"{get_trusted_web_origin()}/api/v1/calendar/oauth/{provider}/callback"


def _build_push_notification_url(provider: str) -> str:
    return f"{get_trusted_web_origin()}/api/v1/calendar/webhooks/{provider}"


def _build_account_redirect(status_value: str, provider: str) -> str:
    provider_redirects = ACCOUNT_REDIRECT_PATHS.get(provider)
    if provider_redirects is None:
        provider_redirects = ACCOUNT_REDIRECT_PATHS[CalendarProvider.GOOGLE.value]
    return provider_redirects.get(status_value, provider_redirects["error"])
