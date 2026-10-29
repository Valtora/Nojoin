"""Pinned bytes and metadata are checked without downloads or real caches."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from backend.utils import forced_aligner_manifest as manifest
from backend.utils import forced_aligner_model_utils as assets
from backend.utils.pinned_model_assets import PinnedModelAssets


@pytest.fixture
def tiny_assets(model_cache_env: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    pinned = {**manifest.load_asset_manifest(), "files": []}
    path = assets._assets.snapshot()
    path.mkdir(parents=True)
    for name in sorted(manifest.FILENAMES):
        value = {
            "model_type": "qwen3_asr",
            "thinker_config": {"model_type": "qwen3_forced_aligner"},
        }
        data = (
            json.dumps(value if name == "config.json" else {}).encode()
            if name.endswith(".json")
            else b"pinned"
        )
        (path / name).write_bytes(data)
        pinned["files"].append(
            {
                "filename": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    monkeypatch.setattr(assets, "load_asset_manifest", lambda: pinned)
    monkeypatch.setattr(assets, "manifest_sha256", lambda: "tiny-manifest-identity")
    monkeypatch.setattr(
        PinnedModelAssets,
        "download",
        lambda *_args: pytest.fail("Unexpected network transfer"),
    )
    return path, pinned


def test_correct_bytes_prepare_and_inspect_as_verified(tiny_assets: Any) -> None:
    path, _pinned = tiny_assets
    assert assets.inspect_forced_aligner_model().state == "partial"
    assert assets.prepare_forced_aligner_model(progress=lambda *_args: None) == path
    assert assets.inspect_forced_aligner_model().path == path
    assets.verify_forced_aligner_model(path)


@pytest.mark.parametrize("change", ["missing", "corrupt"])
def test_missing_or_corrupt_weights_are_refused(tiny_assets: Any, change: str) -> None:
    path, _pinned = tiny_assets
    weights = path / "model.safetensors"
    if change == "missing":
        weights.unlink()
    else:
        weights.write_bytes(b"broken")
    with pytest.raises(manifest.ForcedAlignerError, match="failed verification"):
        assets.verify_forced_aligner_model(path)


@pytest.mark.parametrize("key", ["auto_map", "custom_pipelines"])
def test_even_hash_matching_nested_custom_code_metadata_is_refused(
    tiny_assets: Any, key: str
) -> None:
    path, pinned = tiny_assets
    data = json.dumps({"nested": [{key: {"AutoModel": "code.Model"}}]}).encode()
    (path / "tokenizer_config.json").write_bytes(data)
    entry = next(
        item for item in pinned["files"] if item["filename"] == "tokenizer_config.json"
    )
    entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(manifest.ForcedAlignerError, match="failed verification"):
        assets.verify_forced_aligner_model(path)


def test_explicit_preparation_removes_only_inert_upstream_docs(
    tiny_assets: Any,
) -> None:
    path, _pinned = tiny_assets
    for name in ("README.md", ".gitattributes"):
        (path / name).write_text("Upstream repository documentation", encoding="utf-8")
    with pytest.raises(manifest.ForcedAlignerError):
        assets.verify_forced_aligner_model(path)
    assets.prepare_forced_aligner_model(progress=lambda *_args: None)
    assert not (path / "README.md").exists()
    assert not (path / ".gitattributes").exists()
    assert assets.inspect_forced_aligner_model().state == "verified"


def test_unexpected_executable_file_is_never_removed_or_accepted(
    tiny_assets: Any,
) -> None:
    path, _pinned = tiny_assets
    foreign = path / "modeling_custom.py"
    foreign.write_text("raise RuntimeError('must never run')", encoding="utf-8")
    with pytest.raises(manifest.ForcedAlignerError, match="failed verification"):
        assets.prepare_forced_aligner_model(progress=lambda *_args: None)
    assert foreign.exists()
    assert assets.inspect_forced_aligner_model().state != "verified"


def test_asset_mutation_invalidates_existing_receipt(tiny_assets: Any) -> None:
    path, _pinned = tiny_assets
    assets.prepare_forced_aligner_model(progress=lambda *_args: None)
    (path / "model.safetensors").write_bytes(b"broken")
    assert assets.inspect_forced_aligner_model().state == "invalid"
    with pytest.raises(manifest.ForcedAlignerError):
        assets.verify_forced_aligner_model(path)
