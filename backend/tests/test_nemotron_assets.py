from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests.nemotron_asset_helpers import local_files, write_manifest
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.utils import nemotron_manifest as manifest
from backend.utils import nemotron_model_utils as assets


def test_manifest_has_exact_complete_immutable_assets() -> None:
    actual = assets.load_asset_manifest()
    assert actual["revision"] == manifest.REVISION
    assert {entry["filename"] for entry in actual["files"]} == manifest.FILENAMES
    assert next(
        entry["sha256"]
        for entry in actual["files"]
        if entry["filename"] == "model.safetensors"
    ) == ("c074d86335b3b794f8fa5edc25594558f128bdb3914d27806a3a5a2e44963cb6")


def test_incomplete_manifest_is_rejected(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = assets.load_asset_manifest()
    actual["files"][0]["sha256"] = ""
    manifest.MANIFEST_PATH.write_text(json.dumps(actual))
    with pytest.raises(assets.NemotronModelError, match="manifest"):
        assets.load_asset_manifest()


def test_prepare_receipt_then_detect_same_size_blob_change(
    native_assets: tuple,
) -> None:
    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    assert assets.inspect_nemotron_model().state == "partial"
    assets.prepare_nemotron_model()
    assert assets.inspect_nemotron_model().state == "verified"
    weights = snapshot / "model.safetensors"
    original = weights.stat()
    weights.write_bytes(b"x" * original.st_size)
    os.utime(weights, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert assets.inspect_nemotron_model().state == "invalid"
    with pytest.raises(assets.NemotronModelError, match="verification"):
        assets.verify_nemotron_model(snapshot)


def test_receipt_follows_symlink_target_metadata(native_assets: tuple) -> None:
    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    weights = snapshot / "model.safetensors"
    blob = snapshot.parents[1] / "weights-blob"
    weights.rename(blob)
    weights.symlink_to(blob)
    assets.prepare_nemotron_model()
    assert assets.inspect_nemotron_model().state == "verified"
    receipt = json.loads(
        (assets.nemotron_repo_path() / "nojoin-verification.json").read_text()
    )
    assert receipt["files"]["model.safetensors"]["inode"] == blob.stat().st_ino
    assert str(snapshot) not in json.dumps(receipt)
    blob.write_bytes(b"z" * len(payloads["model.safetensors"]))
    assert assets.inspect_nemotron_model().state == "invalid"


@pytest.mark.parametrize("change", ["custom_code", "wrong_geometry", "wrong_class"])
def test_hash_matching_unexpected_configs_are_rejected(
    native_assets: tuple, change: str
) -> None:
    snapshot, payloads = native_assets
    model = json.loads(payloads["config.json"])
    if change == "custom_code":
        model["audio_config"]["auto_map"] = {}
    elif change == "wrong_geometry":
        model["head_config"]["num_speakers"] = 9
    else:
        model["architectures"] = ["OtherModel"]
    payloads["config.json"] = json.dumps(model).encode()
    write_manifest(manifest.MANIFEST_PATH, payloads)
    local_files(snapshot, payloads)
    with pytest.raises(assets.NemotronModelError):
        assets.verify_nemotron_model(snapshot)


@pytest.mark.parametrize(
    ("filename", "section", "field", "invalid"),
    [
        ("processor_config.json", "feature_extractor", "n_fft", 256),
        ("processor_config.json", "feature_extractor", "win_length", 401),
        ("processor_config.json", "feature_extractor", "hop_length", 80),
        ("processor_config.json", "feature_extractor", "preemphasis", 0.5),
        ("processor_config.json", "feature_extractor", "padding_side", "left"),
        ("processor_config.json", "feature_extractor", "padding_value", 1.0),
        ("processor_config.json", None, "streaming_mode", "very_low_latency"),
        ("config.json", "audio_config", "hidden_size", 256),
        ("config.json", "head_config", "subsampling_factor", 4),
        ("config.json", "streaming_config", "subsampling_factor", 4),
    ],
)
def test_hash_matching_wrong_frontend_never_publishes_receipt(
    native_assets: tuple,
    filename: str,
    section: str | None,
    field: str,
    invalid: object,
) -> None:
    snapshot, payloads = native_assets
    config = json.loads(payloads[filename])
    target = config if section is None else config[section]
    target[field] = invalid
    payloads[filename] = json.dumps(config).encode()
    write_manifest(manifest.MANIFEST_PATH, payloads)
    local_files(snapshot, payloads)

    with pytest.raises(assets.NemotronModelError, match="verification"):
        assets.prepare_nemotron_model()

    assert not (assets.nemotron_repo_path() / "nojoin-verification.json").exists()
    assert assets.inspect_nemotron_model().state == "partial"


def test_inspection_is_read_only_and_never_hashes(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, payloads = native_assets
    assert assets.inspect_nemotron_model().state == "missing"
    assert not assets.nemotron_repo_path().parent.exists()
    local_files(snapshot, payloads)
    assets.prepare_nemotron_model()
    monkeypatch.setattr(
        assets, "_file_matches", lambda *args: pytest.fail("health hashed weights")
    )
    assert assets.inspect_nemotron_model().state == "verified"
    receipt = assets.nemotron_repo_path() / "nojoin-verification.json"
    receipt.write_text("invalid json")
    assert assets.inspect_nemotron_model().error == "receipt_or_assets_changed"


def test_receipt_io_errors_are_classified_without_private_paths(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    assets.prepare_nemotron_model()

    def refuse(path: object) -> None:
        raise PermissionError("/private/model-volume/secret-owner")

    monkeypatch.setattr(assets, "read_json", refuse)
    status = assets.inspect_nemotron_model()
    assert status.state == "invalid"
    assert status.error == "receipt_or_assets_changed"


def test_status_imports_and_inspection_do_not_import_model_libraries(
    tmp_path: Path,
) -> None:
    script = """
import sys
class RefuseModels:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'transformers', 'huggingface_hub'}:
            raise AssertionError('Heavy model import: ' + fullname)
sys.meta_path.insert(0, RefuseModels())
from backend.preload_models import check_model_status
assert check_model_status()['nemotron']['verification_state'] == 'missing'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "HF_HUB_CACHE": str(tmp_path / "missing")},
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
