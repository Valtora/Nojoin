"""Provider runtime configuration and unreadable-state reconciliation.

Resolves the effective OAuth credentials for each provider (database row with
environment-variable fallback) and prunes connections/configs whose encrypted
secrets can no longer be decrypted.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import (
    CalendarConnection,
    CalendarProvider,
    CalendarProviderAvailabilityRead,
    CalendarProviderConfig,
    CalendarProviderStatusRead,
)

from .constants import (
    MICROSOFT_COMMON_TENANT,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_ENV_KEYS,
)
from .models_dto import ProviderRuntimeConfig, UnreadableCalendarConnectionState
from .urls import _build_push_notification_url, _build_redirect_uri

logger = logging.getLogger(__package__)


async def _reset_unreadable_provider_configuration(
    db: AsyncSession,
    row: CalendarProviderConfig,
    provider: str,
) -> None:
    logger.warning(
        "Calendar provider secret for %s could not be decrypted; removing stored provider configuration and falling back to environment configuration if available",
        provider,
    )
    await db.delete(row)
    await db.commit()


def _load_connection_token_bundle(
    connection: CalendarConnection,
) -> tuple[str | None, str | None]:
    try:
        return (
            decrypt_secret(connection.access_token_encrypted),
            decrypt_secret(connection.refresh_token_encrypted),
        )
    except ValueError as exc:
        raise UnreadableCalendarConnectionState() from exc


async def _reset_connection_requiring_reconnect(
    db: AsyncSession,
    connection: CalendarConnection,
) -> None:
    logger.warning(
        "Calendar connection %s (%s) contains unreadable encrypted data; removing stored connection and requiring reconnect",
        connection.id,
        connection.provider,
    )
    await db.delete(connection)


async def _prune_unreadable_connections(db: AsyncSession, *, user_id: int) -> None:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.user_id == user_id)
    )
    connections = list((await db.execute(statement)).scalars().unique().all())

    removed_any = False
    for connection in connections:
        try:
            _load_connection_token_bundle(connection)
        except UnreadableCalendarConnectionState:
            await _reset_connection_requiring_reconnect(db, connection)
            removed_any = True

    if removed_any:
        await db.commit()


async def get_provider_runtime_config(
    db: AsyncSession, provider: str
) -> ProviderRuntimeConfig:
    statement = select(CalendarProviderConfig).where(
        CalendarProviderConfig.provider == provider
    )
    row = (await db.execute(statement)).scalar_one_or_none()

    env_keys = PROVIDER_ENV_KEYS[provider]
    env_client_id = os.getenv(env_keys["client_id"]) if env_keys["client_id"] else None
    env_client_secret = (
        os.getenv(env_keys["client_secret"]) if env_keys["client_secret"] else None
    )
    env_tenant_id = os.getenv(env_keys["tenant_id"]) if env_keys["tenant_id"] else None

    if row is not None:
        try:
            db_client_secret = decrypt_secret(row.client_secret_encrypted)
        except ValueError:
            await _reset_unreadable_provider_configuration(db, row, provider)
            return ProviderRuntimeConfig(
                provider=provider,
                client_id=env_client_id,
                client_secret=env_client_secret,
                tenant_id=env_tenant_id or MICROSOFT_COMMON_TENANT,
                enabled=True,
                source="environment" if env_client_id or env_client_secret else "none",
            )

        if row.enabled is False:
            return ProviderRuntimeConfig(
                provider=provider,
                client_id=row.client_id,
                client_secret=db_client_secret,
                tenant_id=row.tenant_id or env_tenant_id or MICROSOFT_COMMON_TENANT,
                enabled=False,
                source="database",
                push_enabled=bool(row.push_enabled),
            )

        uses_database_values = any(
            value for value in (row.client_id, db_client_secret, row.tenant_id)
        )
        return ProviderRuntimeConfig(
            provider=provider,
            client_id=row.client_id or env_client_id,
            client_secret=db_client_secret or env_client_secret,
            tenant_id=row.tenant_id or env_tenant_id or MICROSOFT_COMMON_TENANT,
            enabled=True,
            source="database"
            if uses_database_values
            else ("environment" if env_client_id or env_client_secret else "none"),
            push_enabled=bool(row.push_enabled),
        )

    return ProviderRuntimeConfig(
        provider=provider,
        client_id=env_client_id,
        client_secret=env_client_secret,
        tenant_id=env_tenant_id or MICROSOFT_COMMON_TENANT,
        enabled=True,
        source="environment" if env_client_id or env_client_secret else "none",
    )


async def list_provider_statuses(db: AsyncSession) -> list[CalendarProviderStatusRead]:
    statuses: list[CalendarProviderStatusRead] = []
    for provider in (CalendarProvider.GOOGLE.value, CalendarProvider.MICROSOFT.value):
        runtime_config = await get_provider_runtime_config(db, provider)
        statuses.append(
            CalendarProviderStatusRead(
                provider=provider,
                display_name=PROVIDER_DISPLAY_NAMES[provider],
                configured=runtime_config.configured,
                source=runtime_config.source,
                enabled=runtime_config.enabled,
                redirect_uri=_build_redirect_uri(provider),
                client_id=runtime_config.client_id,
                tenant_id=runtime_config.tenant_id
                if provider == CalendarProvider.MICROSOFT.value
                else None,
                has_client_secret=bool(runtime_config.client_secret),
                push_enabled=runtime_config.push_enabled,
                push_notification_url=_build_push_notification_url(provider),
            )
        )
    return statuses


def _serialise_provider_availability(
    provider_status: CalendarProviderStatusRead,
) -> CalendarProviderAvailabilityRead:
    return CalendarProviderAvailabilityRead(
        provider=provider_status.provider,
        display_name=provider_status.display_name,
        configured=provider_status.configured,
    )


async def update_provider_configuration(
    db: AsyncSession,
    provider: str,
    *,
    client_id: str | None,
    client_secret: str | None,
    tenant_id: str | None,
    enabled: bool | None,
    clear_client_secret: bool,
    push_enabled: bool | None = None,
) -> CalendarProviderStatusRead:
    statement = select(CalendarProviderConfig).where(
        CalendarProviderConfig.provider == provider
    )
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        row = CalendarProviderConfig(provider=provider)

    if client_id is not None:
        row.client_id = client_id.strip() or None
    if enabled is not None:
        row.enabled = enabled
    if push_enabled is not None:
        row.push_enabled = push_enabled
    if provider == CalendarProvider.MICROSOFT.value and tenant_id is not None:
        row.tenant_id = tenant_id.strip() or MICROSOFT_COMMON_TENANT
    if provider == CalendarProvider.GOOGLE.value:
        row.tenant_id = None

    if clear_client_secret:
        row.client_secret_encrypted = None
    elif client_secret is not None:
        stripped_secret = client_secret.strip()
        row.client_secret_encrypted = (
            encrypt_secret(stripped_secret) if stripped_secret else None
        )

    db.add(row)
    await db.commit()

    # A push toggle change should take effect promptly rather than waiting for
    # the periodic renewal task: enabling provisions channels for existing
    # connections, disabling tears them down. Best-effort; the periodic task is
    # the backstop if the queue is unavailable.
    if push_enabled is not None:
        try:
            from backend.celery_app import celery_app

            celery_app.send_task(
                "backend.worker.tasks.renew_calendar_push_channels_task"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not enqueue calendar push reconcile after provider update: %s",
                exc,
            )

    refreshed = await list_provider_statuses(db)
    return next(
        status_item for status_item in refreshed if status_item.provider == provider
    )
