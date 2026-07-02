import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin_user, get_current_user, get_db
from backend.models.calendar import (
    CalendarActionResponse,
    CalendarAuthorisationStart,
    CalendarConnectionRead,
    CalendarDashboardSummaryRead,
    CalendarOverviewRead,
    CalendarProvider,
    CalendarProviderConfigUpdate,
    CalendarProviderStatusRead,
    CalendarSelectionUpdate,
    CalendarSourceColourUpdate,
)
from backend.models.user import User
from backend.services.calendar_push_service import (
    handle_google_notification,
    handle_microsoft_notification,
)
from backend.services.calendar_service import (
    _build_account_redirect,
    disconnect_connection,
    get_dashboard_summary,
    get_overview,
    handle_callback,
    list_provider_statuses,
    refresh_connection_now,
    start_authorisation,
    update_calendar_source_colour,
    update_connection_selection,
    update_provider_configuration,
)
from backend.utils.rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_provider(provider: str) -> str:
    valid_providers = {CalendarProvider.GOOGLE.value, CalendarProvider.MICROSOFT.value}
    if provider not in valid_providers:
        raise HTTPException(status_code=404, detail="Calendar provider not found")
    return provider


def _map_callback_status(
    provider: str, error: str | None, error_description: str | None
) -> str:
    if error == "access_denied":
        return "cancelled"
    if (
        provider == CalendarProvider.MICROSOFT.value
        and error_description
        and "AADSTS50194" in error_description
    ):
        return "tenant-config-error"
    return "error"


@router.get("", response_model=CalendarOverviewRead)
@router.get("/", response_model=CalendarOverviewRead)
async def get_calendar_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarOverviewRead:
    return await get_overview(db, current_user)


@router.get("/dashboard", response_model=CalendarDashboardSummaryRead)
async def get_calendar_dashboard(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    timezone: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarDashboardSummaryRead:
    return await get_dashboard_summary(db, current_user, month, timezone)


@router.post("/oauth/{provider}/start", response_model=CalendarAuthorisationStart)
async def start_calendar_authorisation(
    provider: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarAuthorisationStart:
    provider = _validate_provider(provider)
    await enforce_rate_limit(
        request,
        namespace=f"calendar-oauth-start:{provider}",
        limit=10,
        window_seconds=10 * 60,
        discriminator=str(current_user.id),
        detail="Too many calendar connection attempts. Please try again later.",
    )
    authorisation_url = await start_authorisation(db, provider, current_user)
    return CalendarAuthorisationStart(authorisation_url=authorisation_url)


@router.get("/oauth/{provider}/start")
async def start_calendar_authorisation_redirect(
    provider: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    provider = _validate_provider(provider)
    await enforce_rate_limit(
        request,
        namespace=f"calendar-oauth-start:{provider}",
        limit=10,
        window_seconds=10 * 60,
        discriminator=str(current_user.id),
        detail="Too many calendar connection attempts. Please try again later.",
    )

    try:
        authorisation_url = await start_authorisation(db, provider, current_user)
    except HTTPException as exc:
        status_value = "config-error" if exc.status_code == 400 else "error"
        return RedirectResponse(
            _build_account_redirect(status_value, provider), status_code=303
        )
    except Exception:  # noqa: BLE001
        return RedirectResponse(
            _build_account_redirect("error", provider), status_code=303
        )

    return RedirectResponse(authorisation_url, status_code=303)


@router.get("/oauth/{provider}/callback")
async def calendar_authorisation_callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    provider = _validate_provider(provider)
    await enforce_rate_limit(
        request,
        namespace=f"calendar-oauth-callback:{provider}",
        limit=20,
        window_seconds=10 * 60,
        discriminator=str(current_user.id),
        detail="Too many calendar callback attempts. Please try again later.",
    )

    if error or not code or not state:
        status_value = _map_callback_status(provider, error, error_description)
        return RedirectResponse(
            _build_account_redirect(status_value, provider), status_code=303
        )

    try:
        await handle_callback(db, provider, current_user, code, state)
    except Exception:  # noqa: BLE001
        return RedirectResponse(
            _build_account_redirect("error", provider), status_code=303
        )

    return RedirectResponse(
        _build_account_redirect("success", provider), status_code=303
    )


@router.put(
    "/connections/{connection_id}/calendars", response_model=CalendarConnectionRead
)
async def update_selected_calendars(
    connection_id: int,
    payload: CalendarSelectionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarConnectionRead:
    return await update_connection_selection(db, current_user, connection_id, payload)


@router.put(
    "/connections/{connection_id}/calendars/{calendar_id}/colour",
    response_model=CalendarConnectionRead,
)
@router.put(
    "/connections/{connection_id}/calendars/{calendar_id}/color",
    response_model=CalendarConnectionRead,
)
async def update_calendar_colour(
    connection_id: int,
    calendar_id: int,
    payload: CalendarSourceColourUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarConnectionRead:
    return await update_calendar_source_colour(
        db,
        current_user,
        connection_id,
        calendar_id,
        payload,
    )


@router.post("/connections/{connection_id}/sync", response_model=CalendarConnectionRead)
async def sync_calendar_connection(
    connection_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarConnectionRead:
    await enforce_rate_limit(
        request,
        namespace=f"calendar-sync:{connection_id}",
        limit=12,
        window_seconds=10 * 60,
        discriminator=f"{current_user.id}:{connection_id}",
        detail="Too many manual calendar sync requests. Please wait and try again.",
    )
    return await refresh_connection_now(db, current_user, connection_id)


@router.delete("/connections/{connection_id}", response_model=CalendarActionResponse)
async def delete_calendar_connection(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarActionResponse:
    await disconnect_connection(db, current_user, connection_id)
    return CalendarActionResponse(success=True, detail="Calendar connection removed")


@router.post("/webhooks/google", include_in_schema=False)
async def google_calendar_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Receive Google Calendar push notifications.

    Called by Google, not by an authenticated user, so this endpoint has no user
    auth. Notifications are authenticated by the per-channel token; the payload
    is treated only as a change signal that enqueues an incremental sync.
    """
    await enforce_rate_limit(
        request,
        namespace="calendar-webhook-google",
        limit=300,
        window_seconds=60,
        detail="Too many calendar webhook requests.",
    )
    try:
        await handle_google_notification(
            db,
            channel_id=request.headers.get("X-Goog-Channel-ID"),
            channel_token=request.headers.get("X-Goog-Channel-Token"),
            resource_state=request.headers.get("X-Goog-Resource-State"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to process Google calendar webhook", exc_info=True)
    # Always acknowledge so Google does not mark the channel as failing.
    return Response(status_code=200)


@router.post("/webhooks/microsoft", include_in_schema=False)
async def microsoft_calendar_webhook(
    request: Request,
    validationToken: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Receive Microsoft Graph change notifications.

    Graph validates the subscription by calling this endpoint with a
    ``validationToken`` query parameter that must be echoed back as text/plain
    within 10 seconds. Otherwise the body carries notifications, authenticated
    per-subscription by ``clientState``.
    """
    await enforce_rate_limit(
        request,
        namespace="calendar-webhook-microsoft",
        limit=300,
        window_seconds=60,
        detail="Too many calendar webhook requests.",
    )
    if validationToken is not None:
        # Bound the reflected token. Graph's validation tokens are short
        # URL-encoded strings, so anything larger is not a legitimate call and
        # must not be echoed back. The rate limit above (keyed per client IP)
        # also covers this path without affecting Graph's own validation call.
        if len(validationToken) > 512:
            return Response(status_code=400)
        return PlainTextResponse(validationToken, status_code=200)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = None
    if payload is not None:
        try:
            await handle_microsoft_notification(db, payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to process Microsoft calendar webhook", exc_info=True
            )
    # 202 Accepted is the expected response for Graph change notifications.
    return Response(status_code=202)


@router.get("/admin/providers", response_model=list[CalendarProviderStatusRead])
async def get_calendar_provider_statuses(
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarProviderStatusRead]:
    return await list_provider_statuses(db)


@router.put("/admin/providers/{provider}", response_model=CalendarProviderStatusRead)
async def save_calendar_provider_configuration(
    provider: str,
    payload: CalendarProviderConfigUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarProviderStatusRead:
    provider = _validate_provider(provider)
    return await update_provider_configuration(
        db,
        provider,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        tenant_id=payload.tenant_id,
        enabled=payload.enabled,
        clear_client_secret=payload.clear_client_secret,
        push_enabled=payload.push_enabled,
    )
