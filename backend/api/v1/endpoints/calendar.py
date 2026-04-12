from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin_user, get_current_user, get_db
from backend.models.calendar import (
    CalendarActionResponse,
    CalendarAuthorisationStart,
    CalendarDashboardSummaryRead,
    CalendarOverviewRead,
    CalendarProvider,
    CalendarProviderConfigUpdate,
    CalendarProviderStatusRead,
    CalendarSelectionUpdate,
    CalendarConnectionRead,
)
from backend.models.user import User
from backend.services.calendar_service import (
    _build_account_redirect,
    disconnect_connection,
    get_dashboard_summary,
    get_overview,
    handle_callback,
    list_provider_statuses,
    refresh_connection_now,
    start_authorisation,
    update_connection_selection,
    update_provider_configuration,
)
from backend.utils.rate_limit import enforce_rate_limit


router = APIRouter()


def _validate_provider(provider: str) -> str:
    valid_providers = {CalendarProvider.GOOGLE.value, CalendarProvider.MICROSOFT.value}
    if provider not in valid_providers:
        raise HTTPException(status_code=404, detail="Calendar provider not found")
    return provider


def _map_callback_status(provider: str, error: str | None, error_description: str | None) -> str:
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarDashboardSummaryRead:
    return await get_dashboard_summary(db, current_user, month)


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
        return RedirectResponse(_build_account_redirect(status_value, provider), status_code=303)
    except Exception:
        return RedirectResponse(_build_account_redirect("error", provider), status_code=303)

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
        return RedirectResponse(_build_account_redirect(status_value, provider), status_code=303)

    try:
        await handle_callback(db, provider, current_user, code, state)
    except Exception:
        return RedirectResponse(_build_account_redirect("error", provider), status_code=303)

    return RedirectResponse(_build_account_redirect("success", provider), status_code=303)


@router.put("/connections/{connection_id}/calendars", response_model=CalendarConnectionRead)
async def update_selected_calendars(
    connection_id: int,
    payload: CalendarSelectionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarConnectionRead:
    return await update_connection_selection(db, current_user, connection_id, payload)


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
    )