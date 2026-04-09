import logging
import os
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.api.services.release_service import (
    ReleaseInfo,
    determine_update_status,
    get_release_by_version,
    get_release_catalog,
    get_windows_installer_asset,
)

router = APIRouter()

logger = logging.getLogger(__name__)


class VersionInfo(BaseModel):
    current_version: str
    latest_version: str | None = None
    is_update_available: bool = False
    update_status: str = "unknown"
    release_url: str | None = None
    current_release_url: str | None = None
    latest_published_at: datetime | None = None
    release_source: str = "unknown"
    companion_download_url: str | None = None
    releases: list[ReleaseInfo] = []

_current_version_cache: str | None = None

VERSION_FILE_PATHS = [
    "/app/docs/VERSION",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "docs/VERSION"),
]


def _get_version_from_image_labels() -> str | None:
    """Reads the version from the running container's Docker image labels."""
    try:
        import docker
        client = docker.from_env()
        container = client.containers.get("nojoin-api")
        labels = container.image.labels or {}
        version = labels.get("org.opencontainers.image.version")
        if version:
            return version.lstrip("v").strip()
    except Exception as e:
        logger.debug(f"Could not read version from Docker image labels: {e}")
    return None


def _get_version_from_file() -> str:
    """Fallback: reads version from the docs/VERSION file on the filesystem."""
    for path in VERSION_FILE_PATHS:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip()
        except Exception:
            continue
    return "0.0.0"


def get_local_version() -> str:
    """
    Resolves the current running version.
    Primary: Docker image label (immutable, set at build time by CI/CD).
    Fallback: docs/VERSION on the filesystem.
    """
    global _current_version_cache
    if _current_version_cache:
        return _current_version_cache

    version = _get_version_from_image_labels()
    if version:
        _current_version_cache = version
        return version

    return _get_version_from_file()


@router.get("", response_model=VersionInfo)
async def get_version(refresh: bool = Query(False)):
    current_version = get_local_version()
    release_catalog = await get_release_catalog(force_refresh=refresh)
    latest_release = release_catalog.releases[0] if release_catalog.releases else None
    current_release = get_release_by_version(release_catalog.releases, current_version)
    companion_asset = get_windows_installer_asset(latest_release)
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
        companion_download_url=(
            companion_asset.browser_download_url if companion_asset else None
        ),
        releases=release_catalog.releases,
    )
