"""Real CPU processor construction and rejection before any model load."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from backend.processing import nemotron_model as runtime_module
from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.utils import nemotron_model_utils as assets


@pytest.fixture(autouse=True)
def forbid_auto_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    import transformers

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("native loader used Auto discovery")

    for auto in (
        transformers.AutoProcessor,
        transformers.AutoModel,
        transformers.AutoModelForAudioFrameClassification,
    ):
        monkeypatch.setattr(auto, "from_pretrained", forbidden)


def test_real_native_processors_use_verified_json_and_extract_cpu_features(
    native_assets: tuple,
) -> None:
    import torch
    from transformers.models.nemotron3_diarization.processing_nemotron3_diarization import (
        Nemotron3DiarizationProcessor,
    )
    from transformers.models.nemotron_asr_streaming.feature_extraction_nemotron_asr_streaming import (
        NemotronAsrStreamingFeatureExtractor,
    )

    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    assets.verify_nemotron_model(snapshot)
    processors = runtime_module._build_processors(snapshot)
    assert len(processors) == 3
    assert len({id(processor) for processor in processors}) == 3
    assert len({id(processor.feature_extractor) for processor in processors}) == 3
    for processor in processors:
        assert type(processor) is Nemotron3DiarizationProcessor
        assert type(processor.feature_extractor) is NemotronAsrStreamingFeatureExtractor
        runtime_module._check_processor(processor)

    audio = np.sin(np.arange(16680, dtype=np.float32) * 0.03) * 0.1
    features = processors[0](
        audio,
        sampling_rate=16000,
        is_streaming=True,
        is_first_audio_chunk=True,
        is_last_audio_chunk=False,
        return_tensors="pt",
    )
    assert features["input_features"].shape == (1, 104, 128)
    assert features["attention_mask"].shape == (1, 104)
    assert features["attention_mask"].sum().item() == 104
    assert features["input_features"].dtype == torch.float32
    assert features["input_features"].device.type == "cpu"
    assert torch.isfinite(features["input_features"]).all()
    assert features["num_lookahead_frames"] == 4
    processors[0].feature_extractor.padding_value = 1.0
    assert processors[1].feature_extractor.padding_value == 0.0
    assert processors[2].feature_extractor.padding_value == 0.0
    processors[0].streaming_modes["low_latency"][0] = 99
    assert processors[1].streaming_modes["low_latency"] == [9, 4]
    assert processors[2].streaming_modes["low_latency"] == [9, 4]


@pytest.mark.parametrize("ready_receipt", [False, True])
@pytest.mark.parametrize("extra", ["sidecar", "regular", "directory", "dangling"])
def test_snapshot_extras_refused_before_loading_or_publishing_receipt(
    native_assets: tuple,
    monkeypatch: pytest.MonkeyPatch,
    ready_receipt: bool,
    extra: str,
) -> None:
    import torch

    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    if ready_receipt:
        assets.prepare_nemotron_model()
        assert assets.inspect_nemotron_model().state == "verified"
    planted = snapshot / "audio_tokenizer_config.json"
    if extra == "sidecar":
        other_model = snapshot.parent / "unverified-model"
        other_model.mkdir()
        (other_model / "pytorch_model.bin").write_bytes(b"unverified checkpoint")
        planted.write_text(
            json.dumps(
                {
                    "audio_tokenizer_class": "AutoModel",
                    "audio_tokenizer_name_or_path": str(other_model),
                }
            )
        )
    elif extra == "directory":
        planted.mkdir()
        (planted / "pytorch_model.bin").write_bytes(b"unverified checkpoint")
    elif extra == "dangling":
        planted.symlink_to(snapshot.parent / "absent-sidecar")
    else:
        (snapshot / "unexpected.txt").write_text("unexpected")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid snapshot reached a constructor or checkpoint loader")

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch, "load", forbidden)
    monkeypatch.setattr(runtime_module, "_build_runtime", forbidden)
    monkeypatch.setattr(runtime_module, "_RUNTIMES", {})
    assert assets.inspect_nemotron_model().state == (
        "invalid" if ready_receipt else "partial"
    )
    with pytest.raises(assets.NemotronModelError, match="verification"):
        assets.verify_nemotron_model(snapshot)
    with pytest.raises(assets.NemotronModelError, match="prepared verified"):
        runtime_module.load_nemotron_model(device="cuda")
    assert runtime_module._RUNTIMES == {}
    with pytest.raises(assets.NemotronModelError, match="verification"):
        assets.prepare_nemotron_model()
    assert not (assets.nemotron_repo_path() / "nojoin-verification.json").exists()
