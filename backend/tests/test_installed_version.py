from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.api.services.release_service import ReleaseCatalog, ReleaseInfo
from backend.api.v1.endpoints import version as version_endpoint
from backend.utils import version as version_utils


@pytest.fixture(autouse=True)
def reset_version_cache() -> None:
    version_utils.reset_installed_version_cache()
    yield
    version_utils.reset_installed_version_cache()


def test_get_installed_version_prefers_embedded_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    embedded_file = tmp_path / ".build-version"
    embedded_file.write_text("0.8.1\n", encoding="utf-8")

    monkeypatch.setattr(version_utils, "_candidate_version_paths", lambda: [embedded_file])
    monkeypatch.setenv(version_utils.BUILD_VERSION_ENV_VAR, "v0.8.3")

    assert version_utils.get_installed_version() == "0.8.3"


def test_get_installed_version_prefers_embedded_file_before_docs_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    embedded_file = tmp_path / ".build-version"
    docs_version = tmp_path / "docs" / "VERSION"
    docs_version.parent.mkdir(parents=True, exist_ok=True)

    embedded_file.write_text("v0.8.4\n", encoding="utf-8")
    docs_version.write_text("0.8.2\n", encoding="utf-8")

    monkeypatch.setattr(
        version_utils,
        "_candidate_version_paths",
        lambda: [embedded_file, docs_version],
    )
    monkeypatch.delenv(version_utils.BUILD_VERSION_ENV_VAR, raising=False)

    assert version_utils.get_installed_version() == "0.8.4"


def test_get_installed_version_returns_default_when_no_sources_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_utils, "_candidate_version_paths", lambda: [])
    monkeypatch.delenv(version_utils.BUILD_VERSION_ENV_VAR, raising=False)

    assert version_utils.get_installed_version() == version_utils.DEFAULT_VERSION


@pytest.mark.anyio
async def test_version_endpoint_uses_installed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_at = datetime(2026, 4, 15, tzinfo=timezone.utc)
    installed_release = ReleaseInfo(
        version="0.8.2",
        tag_name="v0.8.2",
        html_url="https://example.invalid/releases/v0.8.2",
        published_at=published_at,
        body="Installed release",
    )
    latest_release = ReleaseInfo(
        version="0.8.3",
        tag_name="v0.8.3",
        html_url="https://example.invalid/releases/v0.8.3",
        published_at=published_at,
        body="Latest release",
    )

    async def fake_release_catalog(force_refresh: bool = False) -> ReleaseCatalog:
        assert force_refresh is False
        return ReleaseCatalog(
            source="github-releases",
            latest_version="0.8.3",
            latest_release_url=latest_release.html_url,
            latest_published_at=published_at,
            releases=[latest_release, installed_release],
        )

    monkeypatch.setattr(version_endpoint, "get_installed_version", lambda: "0.8.2")
    monkeypatch.setattr(version_endpoint, "get_release_catalog", fake_release_catalog)

    response = await version_endpoint.get_version(refresh=False)

    assert response.current_version == "0.8.2"
    assert response.latest_version == "0.8.3"
    assert response.current_release_url == installed_release.html_url
    assert response.release_url == latest_release.html_url
    assert response.update_status == "update-available"