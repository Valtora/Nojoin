from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.api.services.release_service import (
    ReleaseInfo,
    determine_update_status,
    get_release_by_version,
    get_release_catalog,
)
from backend.utils.version import get_installed_version

router = APIRouter()


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


@router.get("", response_model=VersionInfo)
async def get_version(refresh: bool = Query(False)):
    current_version = get_installed_version()
    release_catalog = await get_release_catalog(force_refresh=refresh)
    current_release = get_release_by_version(release_catalog.releases, current_version)
    update_status = determine_update_status(current_version, release_catalog.latest_version)

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
