"""Exercise real Hub cache handling with only metadata and byte transport stubbed."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.utils import nemotron_manifest as manifest
from backend.utils import nemotron_model_utils as assets


@dataclass
class HubTransport:
    payloads: dict[str, bytes]
    calls: list[dict[str, Any]] = field(default_factory=list)
    transfers: list[str] = field(default_factory=list)
    bodies: dict[str, bytes] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)

    def metadata(self, **kwargs: Any) -> tuple[Any, ...]:
        filename = kwargs["filename"]
        payload = self.payloads[filename]
        return (
            f"https://hub.invalid/{filename}",
            hashlib.sha256(payload).hexdigest(),
            manifest.REVISION,
            len(payload),
            None,
            None,
        )

    def transfer(self, url: str, output: BinaryIO, **kwargs: Any) -> None:
        filename = url.rsplit("/", 1)[-1]
        self.transfers.append(filename)
        body = self.bodies.get(filename, self.payloads[filename])
        if self.failures.get(filename, 0):
            self.failures[filename] -= 1
            output.write(body[:3])
            raise OSError("interrupted transport")
        output.write(body)


@pytest.fixture
def hub_transport(
    native_assets: tuple[Path, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> HubTransport:
    import huggingface_hub
    from huggingface_hub import constants, file_download

    transport = HubTransport(native_assets[1])
    original_download = huggingface_hub.hf_hub_download

    def download(**kwargs: Any) -> str:
        transport.calls.append(kwargs)
        return original_download(**kwargs)

    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", False)
    monkeypatch.setattr(constants, "HF_HUB_DISABLE_SYMLINKS", False)
    # Cache tests must not read/refresh the developer's OAuth credentials.
    monkeypatch.setattr(constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", True)
    monkeypatch.setattr(constants, "HF_HUB_DISABLE_TELEMETRY", True)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    monkeypatch.setattr(
        file_download, "_get_metadata_or_catch_error", transport.metadata
    )
    monkeypatch.setattr(file_download, "http_get", transport.transfer)
    return transport


def test_hub_preparation_pins_manifest_files_and_managed_cache(
    native_assets: tuple[Path, dict[str, bytes]], hub_transport: HubTransport
) -> None:
    snapshot, payloads = native_assets
    assert assets.prepare_nemotron_model() == snapshot
    assert {call["filename"] for call in hub_transport.calls} == manifest.FILENAMES
    assert len(hub_transport.calls) == 3
    for call in hub_transport.calls:
        assert call["repo_id"] == manifest.REPOSITORY
        assert call["revision"] == manifest.REVISION
        assert Path(call["cache_dir"]) == snapshot.parents[2]
        assert "local_dir" not in call
    assert {path.name for path in snapshot.iterdir()} == manifest.FILENAMES
    for filename, payload in payloads.items():
        assert (snapshot / filename).is_symlink()
        assert (snapshot / filename).read_bytes() == payload
    assert assets.inspect_nemotron_model().state == "verified"


@pytest.mark.parametrize("failure", ["short", "oversize", "hash"])
def test_downloaded_size_or_hash_mismatch_never_publishes_receipt(
    native_assets: tuple[Path, dict[str, bytes]],
    hub_transport: HubTransport,
    failure: str,
) -> None:
    _, payloads = native_assets
    body = payloads["model.safetensors"]
    hub_transport.bodies["model.safetensors"] = {
        "short": body[:-1],
        "oversize": body + b"x",
        "hash": b"x" * len(body),
    }[failure]
    progress: list[int] = []
    with pytest.raises(assets.NemotronModelError, match="verification"):
        assets.prepare_nemotron_model(
            progress=lambda message, percent: progress.append(percent)
        )
    assert not (assets.nemotron_repo_path() / "nojoin-verification.json").exists()
    assert assets.inspect_nemotron_model().state == "partial"
    assert 100 not in progress


def test_interrupted_preparation_reuses_completed_hub_files_on_retry(
    native_assets: tuple[Path, dict[str, bytes]], hub_transport: HubTransport
) -> None:
    snapshot, payloads = native_assets
    hub_transport.failures["processor_config.json"] = 1
    with pytest.raises(assets.NemotronModelError, match="transfer"):
        assets.prepare_nemotron_model()
    assert (snapshot / "config.json").read_bytes() == payloads["config.json"]
    assert not (assets.nemotron_repo_path() / "nojoin-verification.json").exists()
    assert assets.inspect_nemotron_model().state == "partial"

    assets.prepare_nemotron_model()
    assert hub_transport.transfers == [
        "config.json",
        "processor_config.json",
        "processor_config.json",
        "model.safetensors",
    ]
    assert assets.inspect_nemotron_model().state == "verified"


@pytest.mark.parametrize("corruption", ["regular", "blob", "external", "broken"])
def test_hub_repairs_invalid_snapshot_entries_without_writing_external_target(
    native_assets: tuple[Path, dict[str, bytes]],
    hub_transport: HubTransport,
    tmp_path: Path,
    corruption: str,
) -> None:
    snapshot, payloads = native_assets
    assets.prepare_nemotron_model()
    weights = snapshot / "model.safetensors"
    external = tmp_path / "external-private-blob"
    external.write_bytes(b"external contents must stay untouched")
    if corruption == "blob":
        weights.write_bytes(b"x" * len(payloads[weights.name]))
    else:
        weights.unlink()
        if corruption == "regular":
            weights.write_bytes(b"legacy corrupt snapshot")
        else:
            weights.symlink_to(
                external if corruption == "external" else tmp_path / "missing"
            )
    hub_transport.calls.clear()
    hub_transport.transfers.clear()

    assets.prepare_nemotron_model()
    assert hub_transport.transfers == [weights.name]
    assert hub_transport.calls[0]["force_download"] is True
    assert weights.is_symlink()
    assert weights.read_bytes() == payloads[weights.name]
    assert external.read_bytes() == b"external contents must stay untouched"
    assert assets.inspect_nemotron_model().state == "verified"


@pytest.mark.parametrize("corruption", ["missing", "hash"])
def test_offline_flag_after_hub_import_prevents_any_download_call(
    native_assets: tuple[Path, dict[str, bytes]],
    hub_transport: HubTransport,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    import huggingface_hub
    from huggingface_hub import constants

    snapshot, payloads = native_assets
    if corruption == "hash":
        local_files(snapshot, payloads)
        (snapshot / "model.safetensors").write_bytes(
            b"x" * len(payloads["model.safetensors"])
        )
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def forbidden(**kwargs: Any) -> None:
        pytest.fail("Offline preparation called hf_hub_download")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", forbidden)
    with pytest.raises(assets.NemotronModelError, match="not prepared.*offline"):
        assets.prepare_nemotron_model()
    assert not (assets.nemotron_repo_path() / "nojoin-verification.json").exists()
    assert not hub_transport.transfers


def test_valid_offline_files_rebuild_receipt_without_hub(
    native_assets: tuple[Path, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub

    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    receipt = assets.nemotron_repo_path() / "nojoin-verification.json"
    receipt.write_text("invalid receipt")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def forbidden(**kwargs: Any) -> None:
        pytest.fail("Valid offline files called hf_hub_download")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", forbidden)
    assert assets.prepare_nemotron_model() == snapshot
    assert assets.inspect_nemotron_model().state == "verified"
