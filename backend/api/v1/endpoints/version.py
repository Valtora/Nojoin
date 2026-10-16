import hashlib
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import (
    get_current_admin_user,
    get_current_session_id,
    get_current_user,
    get_db,
)
from backend.api.services.release_service import (
    ReleaseInfo,
    compare_versions,
    determine_update_status,
    get_release_by_version,
    get_release_catalog,
    normalise_version,
)
from backend.models.user import User
from backend.utils.version import get_installed_version

router = APIRouter()

# Per-user record of how the update notice was dismissed, kept in User.settings:
#   skipped_version  - the newest release the user chose to skip
#   reminded_version - the release the user asked to be reminded about
#   reminded_session - fingerprint of the login session that asked
UPDATE_NOTICE_SETTINGS_KEY = "update_notice"


class VersionInfo(BaseModel):
    current_version: str
    latest_version: str | None = None
    is_update_available: bool = False
    update_status: str = "unknown"
    release_url: str | None = None
    current_release_url: str | None = None
    latest_published_at: datetime | None = None
    release_source: str = "unknown"
    releases: list[ReleaseInfo] = []


class UpdateNotice(BaseModel):
    show: bool = False
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None


class UpdateNoticeDismissal(BaseModel):
    action: Literal["skip", "remind"]
    version: str


@router.get("", response_model=VersionInfo)
async def get_version(refresh: bool = Query(False)):
    current_version = get_installed_version()
    release_catalog = await get_release_catalog(force_refresh=refresh)
    current_release = get_release_by_version(release_catalog.releases, current_version)
    update_status = determine_update_status(
        current_version, release_catalog.latest_version
    )

    return VersionInfo(
        current_version=current_version,
        latest_version=release_catalog.latest_version,
        is_update_available=update_status == "update-available",
        update_status=update_status,
        release_url=release_catalog.latest_release_url,
        current_release_url=current_release.html_url if current_release else None,
        latest_published_at=release_catalog.latest_published_at,
        release_source=release_catalog.source,
        releases=release_catalog.releases,
    )


def _can_manage_updates(user: User) -> bool:
    # Only an owner or admin can upgrade an install, so only they are told.
    return user.role in ("owner", "admin") or bool(user.is_superuser)


def _session_fingerprint(session_id: str) -> str:
    # User settings are returned to the browser and written into backups, so
    # they hold a digest of the session's jti rather than the jti itself.
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _notice_state(user: User) -> dict[str, Any]:
    state = (user.settings or {}).get(UPDATE_NOTICE_SETTINGS_KEY)
    return dict(state) if isinstance(state, dict) else {}


def is_update_notice_dismissed(
    state: dict[str, Any], latest_version: str, session_id: str | None
) -> bool:
    """Whether the user has dismissed the notice for ``latest_version``.

    Skip covers that release and every older one, so the next release shows the
    notice again. Remind Me covers one release for one login session only.
    """
    skipped = compare_versions(latest_version, state.get("skipped_version"))
    if skipped is not None and skipped <= 0:
        return True

    return (
        session_id is not None
        and state.get("reminded_version") == latest_version
        and state.get("reminded_session") == _session_fingerprint(session_id)
    )


@router.get("/update-notice", response_model=UpdateNotice)
async def get_update_notice(
    current_user: User = Depends(get_current_user),
    session_id: str | None = Depends(get_current_session_id),
) -> UpdateNotice:
    current_version = get_installed_version()
    if not _can_manage_updates(current_user):
        return UpdateNotice(current_version=current_version)

    release_catalog = await get_release_catalog()
    latest_version = release_catalog.latest_version
    show = (
        latest_version is not None
        and determine_update_status(current_version, latest_version)
        == "update-available"
        and not is_update_notice_dismissed(
            _notice_state(current_user), latest_version, session_id
        )
    )

    return UpdateNotice(
        show=show,
        current_version=current_version,
        latest_version=latest_version,
        release_url=release_catalog.latest_release_url,
    )


@router.post("/update-notice", status_code=204)
async def dismiss_update_notice(
    dismissal: UpdateNoticeDismissal,
    current_user: User = Depends(get_current_admin_user),
    session_id: str | None = Depends(get_current_session_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    version = normalise_version(dismissal.version)
    if version is None:
        raise HTTPException(status_code=400, detail="Invalid release version.")

    state = _notice_state(current_user)
    if dismissal.action == "skip":
        # A stale tab skipping an older release must not unskip a newer one.
        previous = compare_versions(version, state.get("skipped_version"))
        if previous is None or previous > 0:
            state["skipped_version"] = version
    else:
        if session_id is None:
            raise HTTPException(
                status_code=400,
                detail="Remind Me applies to a browser session.",
            )
        state["reminded_version"] = version
        state["reminded_session"] = _session_fingerprint(session_id)

    # A new dict, so the JSONB column registers the change.
    current_user.settings = {
        **(current_user.settings or {}),
        UPDATE_NOTICE_SETTINGS_KEY: state,
    }
    db.add(current_user)
    await db.commit()
    return Response(status_code=204)
