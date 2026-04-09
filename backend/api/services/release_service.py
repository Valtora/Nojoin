from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

GITHUB_RELEASES_API_URL = "https://api.github.com/repos/Valtora/Nojoin/releases"
GITHUB_RELEASES_PAGE_URL = "https://github.com/Valtora/Nojoin/releases"
GITHUB_LATEST_RELEASE_URL = "https://github.com/Valtora/Nojoin/releases/latest"
GITHUB_RAW_VERSION_URL = "https://raw.githubusercontent.com/Valtora/Nojoin/main/docs/VERSION"
GHCR_TOKEN_URL = "https://ghcr.io/token?scope=repository:valtora/nojoin-api:pull"
GHCR_TAGS_URL = "https://ghcr.io/v2/valtora/nojoin-api/tags/list"

RELEASE_CACHE_TTL = timedelta(minutes=10)
MAX_RELEASE_HISTORY = 12
SEMVER_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_release_cache = {
    "data": None,
    "expires_at": datetime.min,
}
_release_cache_lock = asyncio.Lock()


class ReleaseAssetInfo(BaseModel):
    name: str
    browser_download_url: str
    content_type: str | None = None
    size: int | None = None


class ReleaseInfo(BaseModel):
    version: str
    tag_name: str
    name: str | None = None
    html_url: str
    published_at: datetime | None = None
    body: str | None = None
    draft: bool = False
    prerelease: bool = False
    assets: list[ReleaseAssetInfo] = []


class ReleaseCatalog(BaseModel):
    source: str
    latest_version: str | None = None
    latest_release_url: str | None = None
    latest_published_at: datetime | None = None
    releases: list[ReleaseInfo] = []


def normalise_version(value: str | None) -> str | None:
    if not value:
        return None

    match = SEMVER_PATTERN.match(value.strip())
    if not match:
        return None

    return ".".join(match.groups())


def parse_semver(value: str | None) -> tuple[int, int, int] | None:
    normalised = normalise_version(value)
    if not normalised:
        return None

    major, minor, patch = normalised.split(".")
    return int(major), int(minor), int(patch)


def compare_versions(left: str | None, right: str | None) -> int | None:
    left_parts = parse_semver(left)
    right_parts = parse_semver(right)

    if not left_parts or not right_parts:
        return None

    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def determine_update_status(current_version: str | None, latest_version: str | None) -> str:
    comparison = compare_versions(current_version, latest_version)
    if comparison is None:
        return "unknown"
    if comparison < 0:
        return "update-available"
    if comparison > 0:
        return "ahead"
    return "current"


def get_release_by_version(releases: list[ReleaseInfo], version: str | None) -> ReleaseInfo | None:
    normalised = normalise_version(version)
    if not normalised:
        return None

    for release in releases:
        if release.version == normalised:
            return release
    return None


def get_windows_installer_asset(release: ReleaseInfo | None) -> ReleaseAssetInfo | None:
    if not release:
        return None

    for asset in release.assets:
        asset_name = asset.name.lower()
        if asset_name.endswith(".exe") and "portable" not in asset_name:
            return asset
    return None


def _parse_release_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _release_sort_key(release: ReleaseInfo) -> tuple[int, int, int]:
    return parse_semver(release.version) or (0, 0, 0)


async def _fetch_github_releases(limit: int = MAX_RELEASE_HISTORY) -> list[ReleaseInfo]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Nojoin",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
            response = await client.get(GITHUB_RELEASES_API_URL, params={"per_page": limit})
            if response.status_code != 200:
                logger.debug("GitHub releases request failed: %s", response.status_code)
                return []

            payload = response.json()
    except Exception:
        logger.debug("Failed to fetch GitHub releases metadata.", exc_info=True)
        return []

    releases: list[ReleaseInfo] = []
    for item in payload:
        version = normalise_version(item.get("tag_name"))
        if not version:
            continue

        if item.get("draft") or item.get("prerelease"):
            continue

        assets = [
            ReleaseAssetInfo(
                name=asset.get("name", "Unnamed asset"),
                browser_download_url=asset.get("browser_download_url", ""),
                content_type=asset.get("content_type"),
                size=asset.get("size"),
            )
            for asset in item.get("assets", [])
            if asset.get("browser_download_url")
        ]

        releases.append(
            ReleaseInfo(
                version=version,
                tag_name=item.get("tag_name", version),
                name=item.get("name"),
                html_url=item.get("html_url", GITHUB_RELEASES_PAGE_URL),
                published_at=_parse_release_datetime(item.get("published_at")),
                body=item.get("body") or None,
                draft=bool(item.get("draft", False)),
                prerelease=bool(item.get("prerelease", False)),
                assets=assets,
            )
        )

    releases.sort(key=_release_sort_key, reverse=True)
    return releases[:limit]


async def _fetch_latest_from_ghcr() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            token_resp = await client.get(GHCR_TOKEN_URL)
            if token_resp.status_code != 200:
                logger.debug("GHCR token request failed: %s", token_resp.status_code)
                return None

            token = token_resp.json().get("token")
            if not token:
                return None

            headers = {"Authorization": f"Bearer {token}"}
            tags_resp = await client.get(GHCR_TAGS_URL, headers=headers)
            if tags_resp.status_code != 200:
                logger.debug("GHCR tags request failed: %s", tags_resp.status_code)
                return None

            all_tags = tags_resp.json().get("tags", [])
            semver_tags = [tag for tag in all_tags if normalise_version(tag)]
            if not semver_tags:
                return None

            semver_tags.sort(key=lambda value: parse_semver(value) or (0, 0, 0), reverse=True)
            return normalise_version(semver_tags[0])
    except Exception:
        logger.debug("Failed to fetch latest version from GHCR.", exc_info=True)
        return None


async def _fetch_latest_from_github_raw() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(GITHUB_RAW_VERSION_URL)
            if response.status_code == 200:
                return normalise_version(response.text.strip())
    except Exception:
        logger.debug("Failed to fetch version from GitHub raw.", exc_info=True)
    return None


async def get_release_catalog(force_refresh: bool = False) -> ReleaseCatalog:
    global _release_cache

    async with _release_cache_lock:
        if (
            not force_refresh
            and _release_cache["data"]
            and datetime.now() < _release_cache["expires_at"]
        ):
            return _release_cache["data"]

    releases = await _fetch_github_releases()
    if releases:
        catalog = ReleaseCatalog(
            source="github-releases",
            latest_version=releases[0].version,
            latest_release_url=releases[0].html_url,
            latest_published_at=releases[0].published_at,
            releases=releases,
        )
    else:
        latest_version = await _fetch_latest_from_ghcr()
        source = "ghcr"
        if not latest_version:
            latest_version = await _fetch_latest_from_github_raw()
            source = "github-raw" if latest_version else "unknown"

        if not latest_version:
            logger.warning("Could not determine latest version from GitHub releases, GHCR, or GitHub raw.")

        catalog = ReleaseCatalog(
            source=source,
            latest_version=latest_version,
            latest_release_url=GITHUB_LATEST_RELEASE_URL if latest_version else GITHUB_RELEASES_PAGE_URL,
            releases=[],
        )

    async with _release_cache_lock:
        _release_cache["data"] = catalog
        _release_cache["expires_at"] = datetime.now() + RELEASE_CACHE_TTL

    return catalog