"""Refused grants stop calendar retries, and failed push channels back off.

A refresh token the provider refuses for good (#661) must leave the account in
``reauthorisation_required``, out of the periodic sync, the push sweep and the
reconcile after a sync, instead of asking the token endpoint again every 15
minutes. A push channel the provider refuses (#662) must wait its backoff even
when the same failure is written again. Both run against SQLite through the
real ORM, because the second bug lived in what SQLAlchemy chose not to send.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.core.encryption import encrypt_secret
from backend.models.calendar import CalendarConnection
from backend.services import calendar_push_service as push
from backend.services.calendar_service import oauth as calendar_oauth
from backend.services.calendar_service import sync as calendar_sync
from backend.services.calendar_service.models_dto import ProviderRuntimeConfig
from backend.tests.sqlite_schemas import (
    CALENDAR_CONNECTIONS_SCHEMA,
    CALENDAR_PROVIDER_CONFIGS_SCHEMA,
    CALENDAR_PUSH_CHANNELS_SCHEMA,
    CALENDAR_SOURCES_SCHEMA,
)

SEEDED_AT = datetime(2026, 9, 8, 15, 0)
ATTEMPT_AT = datetime(2026, 10, 23, 12, 0)
RECONNECT_MESSAGE = (
    "This calendar account needs to be reconnected before it can sync again."
)
GRAPH_VALIDATION_FAILED = {
    "error": {
        "code": "InvalidRequest",
        "message": (
            "Subscription validation request failed. Notification endpoint must "
            "respond with 200 OK to validation request."
        ),
    }
}
GRAPH_DETAIL = (
    "HTTP 400 InvalidRequest: Subscription validation request failed. "
    "Notification endpoint must respond with 200 OK to validation request."
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'calendar-reconnect.sqlite3'}",
        future=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        for schema in (
            CALENDAR_CONNECTIONS_SCHEMA,
            CALENDAR_SOURCES_SCHEMA,
            CALENDAR_PROVIDER_CONFIGS_SCHEMA,
            CALENDAR_PUSH_CHANNELS_SCHEMA,
        ):
            await connection.execute(text(schema))
    yield engine
    await engine.dispose()


@pytest.fixture
def session_maker(engine, monkeypatch):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(calendar_sync, "async_session_maker", maker)
    monkeypatch.setattr(push, "async_session_maker", maker)
    return maker


def _runtime_config(provider: str) -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        provider=provider,
        client_id="client",
        client_secret="secret",
        tenant_id=None,
        enabled=True,
        source="test",
        push_enabled=True,
    )


async def _runtime_config_lookup(_db, provider: str) -> ProviderRuntimeConfig:
    return _runtime_config(provider)


async def _seed_connection(
    engine, *, connection_id: int, provider: str, sync_status: str
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO calendar_connections (
                    id, created_at, updated_at, user_id, provider,
                    provider_account_id, refresh_token_encrypted,
                    granted_scopes, sync_status
                ) VALUES (
                    :id, :now, :now, 1, :provider, :account, :refresh, '[]',
                    :sync_status
                )
                """
            ),
            {
                "id": connection_id,
                "now": SEEDED_AT,
                "provider": provider,
                "account": f"account-{connection_id}",
                "refresh": encrypt_secret("stored-refresh-token"),
                "sync_status": sync_status,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO calendar_sources (
                    id, created_at, updated_at, connection_id,
                    provider_calendar_id, name, is_primary, is_read_only,
                    is_selected
                ) VALUES (:id, :now, :now, :id, :remote_id, 'Work', 1, 0, 1)
                """
            ),
            {
                "id": connection_id,
                "now": SEEDED_AT,
                "remote_id": f"cal-{connection_id}",
            },
        )


async def _seed_failed_channel(
    engine, *, connection_id: int, provider: str, last_error: str
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO calendar_push_channels (
                    created_at, updated_at, connection_id, calendar_id, provider,
                    status, last_error
                ) VALUES (:now, :now, :id, :id, :provider, 'failed', :last_error)
                """
            ),
            {
                "now": SEEDED_AT,
                "id": connection_id,
                "provider": provider,
                "last_error": last_error,
            },
        )


async def _scalar(engine, query: str):
    async with engine.connect() as connection:
        return (await connection.execute(text(query))).scalar()


def _token_refusal(provider: str, body: dict) -> httpx.HTTPStatusError:
    url = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    request = httpx.Request("POST", url)
    return httpx.HTTPStatusError(
        f"Client error '400 Bad Request' for url '{url}'",
        request=request,
        response=httpx.Response(400, json=body, request=request),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("google", {"error": "invalid_grant", "error_description": "Bad Request"}),
        (
            "google",
            {
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        ),
        (
            "microsoft",
            {
                "error": "invalid_grant",
                "error_description": (
                    "AADSTS50173: The provided grant has expired due to it being "
                    "revoked, a fresh auth token is needed."
                ),
                "error_codes": [50173],
            },
        ),
    ],
)
async def test_refused_grant_is_reconnect_required_and_asked_once(
    engine, session_maker, monkeypatch, provider, body
) -> None:
    """One refused refresh marks the account, and the reconcile after the sync
    does not ask the token endpoint a second time."""
    await _seed_connection(
        engine, connection_id=1, provider=provider, sync_status="error"
    )
    refresh_attempts: list[str] = []

    async def _refuse(_runtime_config, _refresh_token):
        refresh_attempts.append(provider)
        raise _token_refusal(provider, body)

    monkeypatch.setattr(calendar_oauth, "_refresh_google_access_token", _refuse)
    monkeypatch.setattr(calendar_oauth, "_refresh_microsoft_access_token", _refuse)
    monkeypatch.setattr(
        calendar_sync, "get_provider_runtime_config", _runtime_config_lookup
    )
    monkeypatch.setattr(push, "get_provider_runtime_config", _runtime_config_lookup)

    await calendar_sync.sync_connection_by_id(1)

    assert refresh_attempts == [provider]
    assert (
        await _scalar(engine, "SELECT sync_status FROM calendar_connections")
        == "reauthorisation_required"
    )
    assert (
        await _scalar(engine, "SELECT sync_error FROM calendar_connections")
        == RECONNECT_MESSAGE
    )


@pytest.mark.anyio
async def test_push_sweep_leaves_out_connections_awaiting_reconnect(
    engine, session_maker, monkeypatch
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO calendar_provider_configs (
                    created_at, updated_at, provider, enabled, push_enabled
                ) VALUES (:now, :now, 'google', 1, 1)
                """
            ),
            {"now": SEEDED_AT},
        )
    await _seed_connection(
        engine, connection_id=1, provider="google", sync_status="success"
    )
    await _seed_connection(
        engine,
        connection_id=2,
        provider="google",
        sync_status="reauthorisation_required",
    )
    await _seed_connection(
        engine,
        connection_id=3,
        provider="google",
        sync_status="reauthorisation_required",
    )
    await _seed_failed_channel(
        engine, connection_id=3, provider="google", last_error="HTTP 503"
    )

    # Connection 3 is visited only so its leftover channel can be dropped.
    assert await push._collect_reconcile_connection_ids() == [1, 3]

    async def _no_token(*_args):
        raise AssertionError("a connection awaiting reconnect asked for a token")

    monkeypatch.setattr(push, "_get_access_token_for_connection", _no_token)
    monkeypatch.setattr(push, "get_provider_runtime_config", _runtime_config_lookup)
    for connection_id in (2, 3):
        async with session_maker() as session:
            connection = await session.get(CalendarConnection, connection_id)
            await push.reconcile_push_channels_for_connection(session, connection)

    assert await _scalar(engine, "SELECT count(*) FROM calendar_push_channels") == 0
    assert await push._collect_reconcile_connection_ids() == [1]


@pytest.mark.anyio
async def test_same_push_refusal_written_twice_still_backs_off(
    engine, session_maker, monkeypatch
) -> None:
    """The second identical failure must move the attempt time, or the channel
    is retried on every sync as it was on live."""
    await _seed_connection(
        engine, connection_id=1, provider="microsoft", sync_status="success"
    )
    await _seed_failed_channel(
        engine,
        connection_id=1,
        provider="microsoft",
        # What rows carried before the provider's code and message were kept.
        last_error=(
            "Client error '400 Bad Request' for url "
            "'https://graph.microsoft.com/v1.0/subscriptions'"
        ),
    )
    attempts: list[datetime] = []
    clock = {"now": ATTEMPT_AT}

    async def _access_token(*_args):
        return "access-token"

    async def _refuse(*_args):
        attempts.append(clock["now"])
        request = httpx.Request(
            "POST", "https://graph.microsoft.com/v1.0/subscriptions"
        )
        raise httpx.HTTPStatusError(
            "Client error '400 Bad Request'",
            request=request,
            response=httpx.Response(400, json=GRAPH_VALIDATION_FAILED, request=request),
        )

    monkeypatch.setattr(push, "get_provider_runtime_config", _runtime_config_lookup)
    monkeypatch.setattr(push, "_get_access_token_for_connection", _access_token)
    monkeypatch.setattr(
        push,
        "_build_push_notification_url",
        lambda provider: (
            f"https://nojoin.example.com/api/v1/calendar/webhooks/{provider}"
        ),
    )
    monkeypatch.setattr(push, "_microsoft_create_subscription", _refuse)
    monkeypatch.setattr(push, "utc_now", lambda: clock["now"])

    async def _reconcile_at(when: datetime) -> None:
        clock["now"] = when
        async with session_maker() as session:
            connection = await session.get(CalendarConnection, 1)
            await push.reconcile_push_channels_for_connection(session, connection)

    for hours in (0, 0.25, 0.5, 1, 5.75, 6, 6.25, 7, 11.75, 12):
        await _reconcile_at(ATTEMPT_AT + timedelta(hours=hours))

    # The legacy row retries at once, then each refusal waits six hours.
    assert attempts == [
        ATTEMPT_AT,
        ATTEMPT_AT + timedelta(hours=6),
        ATTEMPT_AT + timedelta(hours=12),
    ]
    assert await _scalar(engine, "SELECT last_error FROM calendar_push_channels") == (
        GRAPH_DETAIL
    )
    stamped = await _scalar(engine, "SELECT updated_at FROM calendar_push_channels")
    assert datetime.fromisoformat(str(stamped)) == ATTEMPT_AT + timedelta(hours=12)


@pytest.mark.anyio
async def test_unreachable_provider_retries_after_the_short_backoff(
    engine, session_maker, monkeypatch
) -> None:
    await _seed_connection(
        engine, connection_id=1, provider="google", sync_status="success"
    )
    attempts: list[datetime] = []
    clock = {"now": ATTEMPT_AT}

    async def _access_token(*_args):
        return "access-token"

    async def _unreachable(*_args):
        attempts.append(clock["now"])
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(push, "get_provider_runtime_config", _runtime_config_lookup)
    monkeypatch.setattr(push, "_get_access_token_for_connection", _access_token)
    monkeypatch.setattr(
        push,
        "_build_push_notification_url",
        lambda provider: (
            f"https://nojoin.example.com/api/v1/calendar/webhooks/{provider}"
        ),
    )
    monkeypatch.setattr(push, "_google_watch", _unreachable)
    monkeypatch.setattr(push, "utc_now", lambda: clock["now"])

    for minutes in (0, 15, 30, 45):
        clock["now"] = ATTEMPT_AT + timedelta(minutes=minutes)
        async with session_maker() as session:
            connection = await session.get(CalendarConnection, 1)
            await push.reconcile_push_channels_for_connection(session, connection)

    assert attempts == [ATTEMPT_AT, ATTEMPT_AT + timedelta(minutes=30)]
    assert await _scalar(engine, "SELECT last_error FROM calendar_push_channels") == (
        "ConnectError: All connection attempts failed"
    )
