from __future__ import annotations

import httpx
import pytest

from backend.api.services import release_service


class _ExplodingAsyncClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_ExplodingAsyncClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        return None

    async def get(self, *args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ReadTimeout("boom")


@pytest.mark.anyio
async def test_fetch_github_releases_returns_empty_list_on_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release_service.httpx, "AsyncClient", _ExplodingAsyncClient)

    releases = await release_service._fetch_github_releases()

    assert releases == []


@pytest.mark.anyio
async def test_fetch_latest_from_github_raw_returns_none_on_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release_service.httpx, "AsyncClient", _ExplodingAsyncClient)

    latest_version = await release_service._fetch_latest_from_github_raw()

    assert latest_version is None
