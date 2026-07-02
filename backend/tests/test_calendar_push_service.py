"""Unit tests for calendar push (webhook) notification handling.

These cover the security-critical decision logic without a database: per-channel
secret validation, renewal/backoff decisions, provider datetime parsing, and the
notification handlers' enqueue behaviour (with the lookup and enqueue helpers
patched out).
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from backend.core.encryption import encrypt_secret
from backend.models.calendar import CalendarProvider, CalendarPushChannelStatus
from backend.services import calendar_push_service as push

NOW = datetime(2026, 7, 2, 12, 0, 0)


def _channel(**overrides) -> SimpleNamespace:
    base = {
        "connection_id": 7,
        "calendar_id": 1,
        "provider": CalendarProvider.GOOGLE.value,
        "provider_channel_id": "chan-1",
        "resource_id": None,
        "secret_encrypted": None,
        "notification_url": None,
        "expiration": None,
        "status": CalendarPushChannelStatus.ACTIVE.value,
        "last_error": None,
        "updated_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_google_expiration_to_datetime_parses_epoch_millis() -> None:
    assert push._google_expiration_to_datetime("1700000000000") == datetime(
        2023, 11, 14, 22, 13, 20
    )
    assert push._google_expiration_to_datetime(None) is None
    assert push._google_expiration_to_datetime("not-a-number") is None


def test_format_graph_datetime_is_utc_zulu_without_microseconds() -> None:
    assert (
        push._format_graph_datetime(datetime(2026, 7, 2, 10, 30, 15, 123456))
        == "2026-07-02T10:30:15Z"
    )


def test_truncate_error_collapses_whitespace_and_caps_length() -> None:
    assert push._truncate_error(None) is None
    assert push._truncate_error("  many   spaces\n here ") == "many spaces here"
    assert len(push._truncate_error("x" * 900)) == 500


def test_secret_matches_only_accepts_the_stored_secret() -> None:
    secret = "channel-secret-token"
    channel = _channel(secret_encrypted=encrypt_secret(secret))
    assert push._secret_matches(channel, secret) is True
    assert push._secret_matches(channel, "wrong") is False
    assert push._secret_matches(channel, None) is False
    assert push._secret_matches(_channel(secret_encrypted=None), secret) is False


def test_needs_renewal_uses_per_provider_thresholds() -> None:
    google_soon = _channel(expiration=NOW + timedelta(hours=1))
    google_far = _channel(expiration=NOW + timedelta(hours=48))
    assert push._needs_renewal(CalendarProvider.GOOGLE.value, google_soon, NOW) is True
    assert push._needs_renewal(CalendarProvider.GOOGLE.value, google_far, NOW) is False

    ms_soon = _channel(expiration=NOW + timedelta(hours=1))
    ms_mid = _channel(expiration=NOW + timedelta(hours=12))
    assert push._needs_renewal(CalendarProvider.MICROSOFT.value, ms_soon, NOW) is True
    assert push._needs_renewal(CalendarProvider.MICROSOFT.value, ms_mid, NOW) is False

    # A NULL expiration is treated as "unknown / assume expired" so the channel
    # is reprovisioned rather than silently left to lapse.
    assert (
        push._needs_renewal(
            CalendarProvider.GOOGLE.value, _channel(expiration=None), NOW
        )
        is True
    )


def test_should_provision_missing_failed_stopped_and_healthy() -> None:
    provider = CalendarProvider.GOOGLE.value
    assert push._should_provision(provider, None, NOW) is True

    failed_recent = _channel(
        status=CalendarPushChannelStatus.FAILED.value,
        updated_at=NOW - timedelta(minutes=5),
    )
    assert push._should_provision(provider, failed_recent, NOW) is False

    failed_old = _channel(
        status=CalendarPushChannelStatus.FAILED.value,
        updated_at=NOW - timedelta(minutes=45),
    )
    assert push._should_provision(provider, failed_old, NOW) is True

    stopped = _channel(status=CalendarPushChannelStatus.STOPPED.value)
    assert push._should_provision(provider, stopped, NOW) is True

    healthy = _channel(expiration=NOW + timedelta(days=5))
    assert push._should_provision(provider, healthy, NOW) is False

    # An ACTIVE channel with an unknown (NULL) expiration must be reprovisioned.
    active_unknown_expiry = _channel(expiration=None)
    assert push._should_provision(provider, active_unknown_expiry, NOW) is True


def test_debounced_enqueue_closes_redis_client_when_set_fails(monkeypatch) -> None:
    """A failing debounce write must still close the Redis client.

    ``redis.from_url`` allocates a fresh connection pool per call; without
    closing it on the error path, every inbound notification during a Redis
    outage would leak a pool until the process runs out of file descriptors.
    """
    import backend.celery_app as celery_mod

    class _FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def set(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

        async def close(self) -> None:
            self.closed = True

    fake = _FakeRedis()
    monkeypatch.setattr(push.redis, "from_url", lambda url: fake)
    sent: list = []
    monkeypatch.setattr(
        celery_mod.celery_app, "send_task", lambda *args, **kwargs: sent.append(1)
    )

    asyncio.run(push._debounced_enqueue_sync(123))

    assert fake.closed is True  # closed even though set() raised
    assert len(sent) == 1  # fell back to enqueuing the sync directly


def _patch_enqueue(monkeypatch) -> list[int]:
    enqueued: list[int] = []

    async def fake_enqueue(connection_id: int) -> None:
        enqueued.append(connection_id)

    monkeypatch.setattr(push, "_debounced_enqueue_sync", fake_enqueue)
    return enqueued


def _patch_find_channel(monkeypatch, channel) -> None:
    async def fake_find_channel(db, provider, provider_channel_id):
        if channel is None:
            return None
        if (
            provider == channel.provider
            and provider_channel_id == channel.provider_channel_id
        ):
            return channel
        return None

    monkeypatch.setattr(push, "_find_channel", fake_find_channel)


def test_google_notification_ignores_initial_sync_state(monkeypatch) -> None:
    enqueued = _patch_enqueue(monkeypatch)
    result = asyncio.run(
        push.handle_google_notification(
            None, channel_id="chan-1", channel_token="tok", resource_state="sync"
        )
    )
    assert result is False
    assert enqueued == []


def test_google_notification_enqueues_for_valid_token(monkeypatch) -> None:
    secret = "google-token"
    channel = _channel(
        provider=CalendarProvider.GOOGLE.value,
        provider_channel_id="chan-9",
        secret_encrypted=encrypt_secret(secret),
        connection_id=11,
    )
    _patch_find_channel(monkeypatch, channel)
    enqueued = _patch_enqueue(monkeypatch)
    result = asyncio.run(
        push.handle_google_notification(
            None, channel_id="chan-9", channel_token=secret, resource_state="exists"
        )
    )
    assert result is True
    assert enqueued == [11]


def test_google_notification_rejects_invalid_token(monkeypatch) -> None:
    channel = _channel(
        provider_channel_id="chan-9",
        secret_encrypted=encrypt_secret("real-token"),
    )
    _patch_find_channel(monkeypatch, channel)
    enqueued = _patch_enqueue(monkeypatch)
    result = asyncio.run(
        push.handle_google_notification(
            None, channel_id="chan-9", channel_token="forged", resource_state="exists"
        )
    )
    assert result is False
    assert enqueued == []


def test_microsoft_notification_enqueues_once_per_connection(monkeypatch) -> None:
    secret = "ms-client-state"
    channel = _channel(
        provider=CalendarProvider.MICROSOFT.value,
        provider_channel_id="sub-1",
        secret_encrypted=encrypt_secret(secret),
        connection_id=42,
    )
    _patch_find_channel(monkeypatch, channel)
    enqueued = _patch_enqueue(monkeypatch)
    payload = {
        "value": [
            {"subscriptionId": "sub-1", "clientState": secret},
            {"subscriptionId": "sub-1", "clientState": secret},
        ]
    }
    count = asyncio.run(push.handle_microsoft_notification(None, payload))
    assert count == 1
    assert enqueued == [42]


def test_microsoft_notification_rejects_invalid_client_state(monkeypatch) -> None:
    channel = _channel(
        provider=CalendarProvider.MICROSOFT.value,
        provider_channel_id="sub-1",
        secret_encrypted=encrypt_secret("real-state"),
    )
    _patch_find_channel(monkeypatch, channel)
    enqueued = _patch_enqueue(monkeypatch)
    payload = {"value": [{"subscriptionId": "sub-1", "clientState": "forged"}]}
    count = asyncio.run(push.handle_microsoft_notification(None, payload))
    assert count == 0
    assert enqueued == []


def test_microsoft_notification_caps_oversized_batch(monkeypatch) -> None:
    """An oversized (untrusted) batch is truncated so it cannot fan out into an
    unbounded number of sequential per-item database lookups."""
    lookups: list[str] = []

    async def counting_find_channel(db, provider, provider_channel_id):
        lookups.append(provider_channel_id)
        return None

    monkeypatch.setattr(push, "_find_channel", counting_find_channel)
    payload = {
        "value": [
            {"subscriptionId": f"sub-{i}", "clientState": "x"} for i in range(150)
        ]
    }

    count = asyncio.run(push.handle_microsoft_notification(None, payload))

    assert count == 0  # _find_channel returned None, so nothing was enqueued
    assert len(lookups) == 100  # only the first 100 of 150 items were processed


def test_microsoft_renewal_recreates_when_address_changes(monkeypatch) -> None:
    """If the webhook address changed, renewing in place would leave Microsoft
    delivering to the old URL (PATCH only updates the expiry). The channel must be
    recreated with the new notificationUrl and the stale subscription deleted."""
    renewed: list[str] = []
    created_with: list[str] = []
    deleted: list[str] = []

    async def fake_renew(access_token, subscription_id, expiration):
        renewed.append(subscription_id)
        return {"expirationDateTime": "2026-07-05T00:00:00Z"}

    async def fake_create(access_token, calendar_id, address, secret, expiration):
        created_with.append(address)
        return {"id": "new-sub", "expirationDateTime": "2026-07-05T00:00:00Z"}

    async def fake_delete(access_token, subscription_id):
        deleted.append(subscription_id)

    monkeypatch.setattr(push, "_microsoft_renew_subscription", fake_renew)
    monkeypatch.setattr(push, "_microsoft_create_subscription", fake_create)
    monkeypatch.setattr(push, "_safe_microsoft_delete", fake_delete)

    new_address = "https://new.example.com/api/v1/calendar/webhooks/microsoft"
    ctx = SimpleNamespace(
        db=SimpleNamespace(add=lambda row: None),
        connection=SimpleNamespace(id=7),
        access_token="tok",
        address=new_address,
    )
    calendar = SimpleNamespace(id=1, provider_calendar_id="cal-1")
    existing = _channel(
        provider=CalendarProvider.MICROSOFT.value,
        provider_channel_id="old-sub",
        status=CalendarPushChannelStatus.ACTIVE.value,
        secret_encrypted=encrypt_secret("s"),
        notification_url="https://old.example.com/api/v1/calendar/webhooks/microsoft",
    )

    asyncio.run(push._ensure_microsoft_channel(ctx, calendar, existing))

    assert renewed == []  # did not renew in place
    assert created_with == [new_address]  # recreated with the new address
    assert deleted == ["old-sub"]  # stale subscription cleaned up
    assert existing.provider_channel_id == "new-sub"
    assert existing.notification_url == new_address


def test_microsoft_renewal_updates_in_place_when_address_unchanged(monkeypatch) -> None:
    renewed: list[str] = []
    created: list[int] = []

    async def fake_renew(access_token, subscription_id, expiration):
        renewed.append(subscription_id)
        return {"expirationDateTime": "2026-07-05T00:00:00Z"}

    async def fake_create(*args, **kwargs):
        created.append(1)
        return {"id": "unused"}

    monkeypatch.setattr(push, "_microsoft_renew_subscription", fake_renew)
    monkeypatch.setattr(push, "_microsoft_create_subscription", fake_create)

    address = "https://same.example.com/api/v1/calendar/webhooks/microsoft"
    ctx = SimpleNamespace(
        db=SimpleNamespace(add=lambda row: None),
        connection=SimpleNamespace(id=7),
        access_token="tok",
        address=address,
    )
    calendar = SimpleNamespace(id=1, provider_calendar_id="cal-1")
    existing = _channel(
        provider=CalendarProvider.MICROSOFT.value,
        provider_channel_id="sub-1",
        status=CalendarPushChannelStatus.ACTIVE.value,
        secret_encrypted=encrypt_secret("s"),
        notification_url=address,
    )

    asyncio.run(push._ensure_microsoft_channel(ctx, calendar, existing))

    assert renewed == ["sub-1"]  # renewed in place, kept the subscription
    assert created == []  # did not recreate


def test_calendar_services_import_without_fastapi(monkeypatch) -> None:
    """The Celery worker image ships no web framework.

    The calendar sync service (and the push service that imports it) must import
    with fastapi absent, otherwise the periodic worker tasks crash on import with
    ModuleNotFoundError instead of syncing.
    """
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError("No module named 'fastapi'")
        return real_import(name, *args, **kwargs)

    for module_name in (
        "backend.services.calendar_service",
        "backend.services.calendar_push_service",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    for module_name in list(sys.modules):
        if module_name == "fastapi" or module_name.startswith("fastapi."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    calendar_service = importlib.import_module("backend.services.calendar_service")
    calendar_push_service = importlib.import_module(
        "backend.services.calendar_push_service"
    )

    assert calendar_service.HTTPException is None
    assert callable(calendar_push_service.reconcile_push_channels_for_connection)


def test_run_calendar_async_disposes_engine(monkeypatch) -> None:
    """The worker helper must dispose the async engine after each run.

    Each Celery task runs asyncio.run in a fresh loop; the pooled async engine
    binds connections to their creating loop, so without disposal the next task
    fails with "got Future attached to a different loop".
    """
    import backend.core.db as db
    from backend.worker.tasks import calendar as calendar_tasks

    disposed: list[bool] = []

    class _FakeEngine:
        async def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(db, "engine", _FakeEngine())

    async def _work() -> int:
        return 42

    result = calendar_tasks._run_calendar_async(_work())

    assert result == 42
    assert disposed == [True]
