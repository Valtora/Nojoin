from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from datetime import datetime, timedelta
import asyncio
import os
import re
import logging

router = APIRouter()

logger = logging.getLogger(__name__)


class VersionInfo(BaseModel):
    current_version: str
    latest_version: str | None = None
    is_update_available: bool = False
    release_url: str | None = None


# In-memory cache for the full version response
_version_cache = {
    "data": None,
    "expires_at": datetime.min
}
_cache_lock = asyncio.Lock()

# Cached current version from Docker labels (static for the container's lifetime)
_current_version_cache: str | None = None

GHCR_TOKEN_URL = "https://ghcr.io/token?scope=repository:valtora/nojoin-api:pull"
GHCR_TAGS_URL = "https://ghcr.io/v2/valtora/nojoin-api/tags/list"
GITHUB_RAW_VERSION_URL = "https://raw.githubusercontent.com/Valtora/Nojoin/main/docs/VERSION"

VERSION_FILE_PATHS = [
    "/app/docs/VERSION",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "docs/VERSION"),
]

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


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


async def _fetch_latest_from_ghcr() -> str | None:
    """
    Queries the GHCR OCI registry for available tags on the nojoin-api image.
    Returns the highest semver tag, or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Obtain anonymous bearer token for the public repository
            token_resp = await client.get(GHCR_TOKEN_URL)
            if token_resp.status_code != 200:
                logger.debug(f"GHCR token request failed: {token_resp.status_code}")
                return None

            token = token_resp.json().get("token")
            if not token:
                return None

            # Fetch the tag list
            headers = {"Authorization": f"Bearer {token}"}
            tags_resp = await client.get(GHCR_TAGS_URL, headers=headers)
            if tags_resp.status_code != 200:
                logger.debug(f"GHCR tags request failed: {tags_resp.status_code}")
                return None

            all_tags = tags_resp.json().get("tags", [])

            # Filter to valid semver tags only (e.g. "0.6.8", not "latest", "sha-abc", "main")
            semver_tags = [t for t in all_tags if SEMVER_PATTERN.match(t)]
            if not semver_tags:
                return None

            # Sort by semver components descending and return the highest
            semver_tags.sort(key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True)
            return semver_tags[0]

    except Exception as e:
        logger.debug(f"Failed to fetch latest version from GHCR: {e}")
        return None


async def _fetch_latest_from_github_raw() -> str | None:
    """Fallback: fetches the latest version from the GitHub raw docs/VERSION file."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(GITHUB_RAW_VERSION_URL)
            if resp.status_code == 200:
                return resp.text.strip().lstrip("v")
    except Exception as e:
        logger.debug(f"Failed to fetch version from GitHub raw: {e}")
    return None


@router.get("", response_model=VersionInfo)
async def get_version():
    global _version_cache

    current_version = get_local_version()

    async with _cache_lock:
        if _version_cache["data"] and datetime.now() < _version_cache["expires_at"]:
            cached_data = _version_cache["data"]
            cached_data.current_version = current_version

            if cached_data.latest_version:
                cur = current_version.lstrip("v")
                lat = cached_data.latest_version.lstrip("v")
                cached_data.is_update_available = cur != lat

            return cached_data

    # Resolve latest version: GHCR registry first, GitHub raw fallback
    latest_version = await _fetch_latest_from_ghcr()
    if not latest_version:
        latest_version = await _fetch_latest_from_github_raw()
        if not latest_version:
            logger.warning("Could not determine latest version from either GHCR or GitHub")

    release_url = "https://github.com/Valtora/Nojoin/pkgs/container/nojoin-api"
    is_update_available = False

    if latest_version:
        clean_current = current_version.lstrip("v")
        if clean_current and clean_current != latest_version:
            is_update_available = True

    info = VersionInfo(
        current_version=current_version,
        latest_version=latest_version,
        is_update_available=is_update_available,
        release_url=release_url
    )

    async with _cache_lock:
        _version_cache["data"] = info
        _version_cache["expires_at"] = datetime.now() + timedelta(minutes=30)

    return info
