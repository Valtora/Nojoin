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
async def test_validate_hf_token_reuses_single_entry_cache_for_same_token(
    monkeypatch,
) -> None:
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


@pytest.mark.anyio
async def test_get_diarization_component_accepts_local_assets_without_hf_token(
    monkeypatch,
) -> None:
    async def _fake_keys(_db):
        return {"hf_token": None}

    async def _fake_validate(_token):
        return {
            "configured": False,
            "valid": None,
            "status": "info",
            "detail": "No Hugging Face token is configured for Pyannote.",
            "action": "Add a Hugging Face token and accept the Pyannote model terms.",
        }

    monkeypatch.setattr(health_service, "async_get_system_api_keys", _fake_keys)
    monkeypatch.setattr(health_service, "_validate_hf_token", _fake_validate)

    component, ready = await health_service._get_diarization_component(
        db=None,
        model_status={
            "pyannote": {"downloaded": True, "source": "bundled"},
            "embedding": {"downloaded": True, "source": "bundled"},
            "segmentation": {"downloaded": True, "source": "bundled"},
        },
        download={"in_progress": False, "stage": None, "status": None},
    )

    assert ready is True
    assert component["status"] == "ok"
    assert component["label"] == "Ready"
    assert component["using_local_assets"] is True
    assert component["token_configured"] is False


def test_storage_component_reports_ok_when_writable(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.utils.recording_storage.probe_recordings_storage",
        lambda: (True, None),
    )

    component, ready = health_service._get_storage_component()

    assert ready is True
    assert component["status"] == "ok"


def test_storage_component_reports_error_when_not_writable(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.utils.recording_storage.probe_recordings_storage",
        lambda: (False, "Permission denied"),
    )

    component, ready = health_service._get_storage_component()

    assert ready is False
    assert component["status"] == "error"
    assert component["detail"] == "Permission denied"
    assert component["action"]


def test_unwritable_storage_blocks_the_pipeline_summary() -> None:
    checks = {
        "database": {"status": "ok"},
        "queue": {"status": "ok"},
        "worker": {"status": "ok"},
        "ffmpeg": {"status": "ok"},
        "storage": {"status": "error"},
        "transcription_model": {"status": "ok"},
        "diarization": {"status": "ok", "enabled": True},
        "device": {"status": "ok"},
        "optional_ai": {"status": "ok"},
    }

    summary = health_service._build_summary(
        checks,
        transcription_ready=True,
        diarization_ready=True,
        device_ready=True,
    )

    assert summary["pipeline_status"] == "blocked"
    assert any("not writable" in reason for reason in summary["blocking_reasons"])
