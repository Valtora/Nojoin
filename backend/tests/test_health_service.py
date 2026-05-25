from __future__ import annotations

import pytest

import backend.api.services.health_service as health_service


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHFClient:
    def __init__(self, *args, **kwargs) -> None:
        self._calls = kwargs.pop("calls")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, _url: str, *, headers: dict[str, str]) -> _FakeResponse:
        token = headers["Authorization"].removeprefix("Bearer ")
        self._calls.append(token)
        return _FakeResponse(200 if token == "hf-valid" else 403)


@pytest.fixture(autouse=True)
def _reset_hf_validation_cache() -> None:
    health_service._hf_validation_cache = None
    yield
    health_service._hf_validation_cache = None


@pytest.mark.anyio
async def test_validate_hf_token_reuses_single_entry_cache_for_same_token(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_async_client(*args, **kwargs):
        return _FakeHFClient(*args, calls=calls, **kwargs)

    monkeypatch.setattr(health_service.httpx, "AsyncClient", _fake_async_client)

    first = await health_service._validate_hf_token("hf-valid")
    second = await health_service._validate_hf_token("hf-valid")

    assert first["valid"] is True
    assert second == first
    assert calls == ["hf-valid"]


@pytest.mark.anyio
async def test_validate_hf_token_revalidates_when_token_changes(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_async_client(*args, **kwargs):
        return _FakeHFClient(*args, calls=calls, **kwargs)

    monkeypatch.setattr(health_service.httpx, "AsyncClient", _fake_async_client)

    first = await health_service._validate_hf_token("hf-valid")
    second = await health_service._validate_hf_token("hf-invalid")

    assert first["valid"] is True
    assert second["valid"] is False
    assert calls == ["hf-valid", "hf-invalid"]