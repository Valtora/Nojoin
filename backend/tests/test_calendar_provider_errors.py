"""How a provider's refusal is described for the log and the settings page.

Connecting an account (#668) and provisioning a push channel (#662) share these
helpers. Their output reaches the API log and the redirect to the settings page,
so it must keep the provider's reason and nothing of the request or account.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from backend.services.calendar_service import (
    CalendarConnectFailed,
    _build_account_redirect,
    _describe_failure_for_log,
    _describe_provider_failure,
)

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def _refusal(status_code: int, body: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", TOKEN_URL, data={"code": "authorisation-code"})
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError("refused", request=request, response=response)


def test_a_token_endpoint_refusal_keeps_its_oauth_code() -> None:
    failure = CalendarConnectFailed(
        "token exchange",
        _refusal(
            401,
            {
                "error": "invalid_client",
                "error_description": "AADSTS7000215: Invalid client secret provided.",
            },
        ),
    )

    assert failure.step == "token exchange"
    assert failure.reason == "invalid_client"
    assert failure.detail == (
        "HTTP 401 invalid_client: AADSTS7000215: Invalid client secret provided."
    )
    assert "authorisation-code" not in failure.detail


def test_a_provider_message_never_carries_an_address() -> None:
    detail = _describe_provider_failure(
        _refusal(
            403,
            {
                "error": {
                    "code": "ErrorAccessDenied",
                    "message": "Access to alice.example@example.com is denied.",
                }
            },
        )
    )

    assert detail == "HTTP 403 ErrorAccessDenied: Access to <address> is denied."


def test_a_reason_that_is_not_a_provider_code_stays_off_the_page() -> None:
    failure = CalendarConnectFailed(
        "calendar list",
        _refusal(403, {"error": {"code": "Call us on 0000", "message": "Denied"}}),
    )

    assert failure.reason is None
    assert _build_account_redirect("error", "google", "Call us on 0000") == (
        "/settings/integrations?calendar=error&provider=google"
    )


@pytest.mark.parametrize("status_value", ["success", "cancelled", "config-error"])
def test_only_a_failed_connection_carries_a_reason(status_value: str) -> None:
    assert _build_account_redirect(status_value, "microsoft", "forbidden") == (
        f"/settings/integrations?calendar={status_value}&provider=microsoft"
    )


def test_a_database_error_is_logged_by_type_alone() -> None:
    # The statement's parameters are part of the message, and can hold calendar
    # names and event text.
    error = IntegrityError(
        "INSERT INTO calendar_sources (name) VALUES (?)", ("Alice's calendar",), None
    )

    assert _describe_failure_for_log(error) == "IntegrityError"


def test_a_network_error_is_logged_in_full() -> None:
    assert (
        _describe_failure_for_log(httpx.ConnectError("All connection attempts failed"))
        == "ConnectError: All connection attempts failed"
    )
