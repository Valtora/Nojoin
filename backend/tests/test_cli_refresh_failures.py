"""A subscription's token refresh: only a rejected grant needs reconnecting (#994).

The live calls' backoff stops a tier for the rest of a capture on a failure
that cannot clear by itself. A refresh the token endpoint rejected is one; a
refresh that did not complete (a network error, a timeout, a 429, a 5xx, or
an answer with no token) is not, and the next request refreshes again.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlmodel import Session

from backend.processing.cli.manager import (
    CliConversationManager,
    CliCredentialUnavailableError,
    CliOAuthUnavailableError,
)
from backend.processing.llm_backends.failures import CANNOT_CLEAR, classify_failure
from backend.services.cli_oauth import oauth
from backend.tests.test_cli_manager import _read, _seed, _sqlite_engine
from backend.utils.time import utc_now


def _exchange_error(status: int | None = None, cause: httpx.HTTPError | None = None):
    if cause is None:
        return oauth.CliOAuthExchangeError(
            f"Token exchange returned HTTP {status}", http_status=status
        )
    try:
        raise cause
    except httpx.HTTPError as exc:
        try:
            raise oauth.CliOAuthExchangeError(
                f"Token endpoint request failed: {exc}"
            ) from exc
        except oauth.CliOAuthExchangeError as wrapped:
            return wrapped


def _refresh_failing_with(monkeypatch, error: Exception):
    engine = _sqlite_engine()
    _seed(engine, expires_at=utc_now() - timedelta(minutes=1), refresh="refresh-old")
    monkeypatch.setattr(
        "backend.processing.cli.manager.get_sync_session", lambda: Session(engine)
    )

    async def _refresh(_token):
        raise error

    monkeypatch.setattr(oauth, "refresh_tokens", _refresh)
    with pytest.raises(CliOAuthUnavailableError) as raised:
        CliConversationManager()._resolve_access_token(1)
    return raised.value, engine


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_rejected_grant_needs_the_subscription_reconnected(monkeypatch, status):
    error, engine = _refresh_failing_with(monkeypatch, _exchange_error(status))

    assert isinstance(error, CliCredentialUnavailableError)
    assert classify_failure(error).kind == "auth"
    assert _read(engine).status == "needs_reauth"


@pytest.mark.parametrize(
    "error",
    [
        _exchange_error(429),
        _exchange_error(500),
        _exchange_error(503),
        # A token endpoint's 404 or 402 says nothing about the model or credit.
        _exchange_error(404),
        _exchange_error(402),
        _exchange_error(cause=httpx.ConnectError("connection refused")),
        _exchange_error(cause=httpx.ReadTimeout("timed out")),
        oauth.CliOAuthExchangeError("Token response did not include an access_token"),
    ],
    ids=["429", "500", "503", "404", "402", "refused", "timeout", "no-token"],
)
def test_a_refresh_that_did_not_complete_is_tried_again(monkeypatch, error):
    raised, _engine = _refresh_failing_with(monkeypatch, error)

    assert not isinstance(raised, CliCredentialUnavailableError)
    failure = classify_failure(raised)
    assert failure.clears_by_itself and failure.kind not in CANNOT_CLEAR


@pytest.mark.parametrize(
    ("cause", "kind"),
    [
        (httpx.ConnectError("refused"), "unreachable"),
        (httpx.ReadTimeout("t"), "timeout"),
    ],
)
def test_a_refresh_network_failure_reads_as_one(monkeypatch, cause, kind):
    raised, _engine = _refresh_failing_with(monkeypatch, _exchange_error(cause=cause))

    assert classify_failure(raised).kind == kind


def test_the_exchange_error_keeps_the_endpoints_status_apart():
    error = _exchange_error(503)

    assert error.http_status == 503 and not error.grant_rejected
    assert _exchange_error(400).grant_rejected
    assert not hasattr(error, "status_code")
