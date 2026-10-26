"""The loader must constrain the native constructors and reject version/config drift."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.processing import nemotron_model as runtime_module
from backend.processing.nemotron_frontend import CudaFrontend
from backend.processing.nemotron_types import NEMOTRON_TRANSFORMERS_VERSION
from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets


@pytest.fixture
def constructor_spies(native_assets: tuple, monkeypatch: pytest.MonkeyPatch) -> Any:
    import torch
    import transformers
    from transformers.models.nemotron3_diarization import (
        modeling_nemotron3_diarization as modeling,
    )

    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    calls: list[tuple[str, str, dict[str, Any]]] = []
    frontend_devices: list[str] = []

    class Model:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                model_type="nemotron3_diarization",
                chunk_length=340,
                chunk_right_context=40,
                fifo_length=40,
                speaker_cache_update_period=300,
                _attn_implementation="sdpa",
                audio_config=SimpleNamespace(
                    hidden_size=512, num_mel_bins=128, subsampling_factor=8
                ),
                head_config=SimpleNamespace(num_speakers=8, subsampling_factor=8),
                streaming_config=SimpleNamespace(
                    fifo_length=264,
                    speaker_cache_length=264,
                    speaker_cache_update_period=222,
                    num_speakers=8,
                    subsampling_factor=8,
                    speaker_cache_silence_frames_per_speaker=1,
                    prediction_score_threshold=0.25,
                    latest_frames_score_boost=0.05,
                    min_positive_scores_rate=0.5,
                    strong_boost_rate=0.75,
                    weak_boost_rate=1.5,
                ),
            )
            self.evaluated = False
            self.device = "unmoved"

        def eval(self) -> Any:
            self.evaluated = True
            return self

        def to(self, device: str) -> Any:
            self.device = device
            return self

        def parameters(self) -> list[Any]:
            return [torch.zeros(1, dtype=torch.float32)]

    def model_load(path: str, **kwargs: Any) -> Any:
        assert torch.backends.cuda.matmul.allow_tf32 is False
        assert torch.backends.cudnn.allow_tf32 is True
        calls.append(("model", path, kwargs))
        return Model()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("native loader used Auto discovery")

    for auto in (
        transformers.AutoProcessor,
        transformers.AutoModel,
        transformers.AutoModelForAudioFrameClassification,
    ):
        monkeypatch.setattr(auto, "from_pretrained", forbidden)
    monkeypatch.setattr(
        Model, "from_pretrained", staticmethod(model_load), raising=False
    )
    monkeypatch.setattr(
        modeling, "Nemotron3DiarizationForAudioFrameClassification", Model
    )

    def cpu_frontend(base: Any, *, device: str) -> CudaFrontend:
        frontend_devices.append(device)
        return CudaFrontend(base, device="cpu")

    monkeypatch.setattr(runtime_module, "CudaFrontend", cpu_frontend)
    monkeypatch.setattr(
        runtime_module.assets, "manifest_sha256", lambda: "asset-digest"
    )
    monkeypatch.setattr(torch.backends.cuda.matmul, "allow_tf32", True)
    monkeypatch.setattr(torch.backends.cudnn, "allow_tf32", False)
    return SimpleNamespace(
        calls=calls, model_class=Model, path=snapshot, frontend_devices=frontend_devices
    )


def test_native_constructors_are_local_safetensors_fp32_sdpa_and_processors_independent(
    constructor_spies: Any,
) -> None:
    import torch

    runtime = runtime_module._build_runtime(
        constructor_spies.path,
        "cuda:3",
        {"transformers_version": NEMOTRON_TRANSFORMERS_VERSION},
    )
    assert constructor_spies.calls == [
        (
            "model",
            str(constructor_spies.path),
            {
                "local_files_only": True,
                "trust_remote_code": False,
                "use_safetensors": True,
                "dtype": torch.float32,
                "attn_implementation": "sdpa",
            },
        ),
    ]
    assert runtime.model.evaluated
    assert runtime.model.device == "cuda:3"
    assert constructor_spies.frontend_devices == ["cuda:3"]
    assert runtime.streaming_processor is not runtime.offline_processor
    assert (
        runtime.offline_processor.feature_extractor
        is not runtime.cpu_streaming_processor.feature_extractor
    )
    assert runtime.asset_fingerprint == "asset-digest"
    assert len(runtime.runtime_fingerprint) == 64
    assert torch.backends.cuda.matmul.allow_tf32 is True
    assert torch.backends.cudnn.allow_tf32 is False


def test_wrong_manifest_version_rejects_before_constructors(
    constructor_spies: Any,
) -> None:
    with pytest.raises(runtime_module.assets.NemotronModelError, match="version"):
        runtime_module._build_runtime(
            Path("/managed"), "cuda", {"transformers_version": "5.19.0"}
        )
    assert constructor_spies.calls == []


def test_wrong_installed_version_rejects_before_constructors(
    constructor_spies: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import transformers

    monkeypatch.setattr(transformers, "__version__", "5.99.0")
    with pytest.raises(runtime_module.assets.NemotronModelError, match="version"):
        runtime_module._build_runtime(
            Path("/managed"),
            "cuda",
            {"transformers_version": NEMOTRON_TRANSFORMERS_VERSION},
        )
    assert constructor_spies.calls == []


def test_changed_feature_geometry_rejected_before_model(
    constructor_spies: Any,
) -> None:
    config_path = constructor_spies.path / "processor_config.json"
    config = json.loads(config_path.read_text())
    config["feature_extractor"]["n_fft"] = 400
    config_path.write_text(json.dumps(config))
    with pytest.raises(runtime_module.assets.NemotronModelError, match="n_fft"):
        runtime_module._build_runtime(
            constructor_spies.path,
            "cuda",
            {"transformers_version": NEMOTRON_TRANSFORMERS_VERSION},
        )
    assert not [call for call in constructor_spies.calls if call[0] == "model"]


def test_unexpected_native_model_class_is_rejected(
    constructor_spies: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        constructor_spies.model_class,
        "from_pretrained",
        lambda *args, **kwargs: SimpleNamespace(
            eval=lambda: SimpleNamespace(to=lambda device: object())
        ),
    )
    with pytest.raises(runtime_module.assets.NemotronModelError, match="model class"):
        runtime_module._build_runtime(
            constructor_spies.path,
            "cuda",
            {"transformers_version": NEMOTRON_TRANSFORMERS_VERSION},
        )


@pytest.mark.parametrize("failing_probe", range(1, 7))
def test_frontend_rejection_is_not_cached_and_success_is_checked_once(
    constructor_spies: Any, monkeypatch: pytest.MonkeyPatch, failing_probe: int
) -> None:
    import torch

    from backend.processing import nemotron_frontend as frontend
    from backend.processing.nemotron_types import NemotronStateError

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(runtime_module, "_RUNTIMES", {})
    monkeypatch.setattr(
        runtime_module.assets,
        "inspect_nemotron_model",
        lambda: SimpleNamespace(state="verified", path=constructor_spies.path),
    )
    compare = frontend.check_feature_equivalence
    calls = 0
    corrupt = True

    def inject_drift(reference: Any, actual: Any) -> None:
        nonlocal calls
        calls += 1
        assert torch.is_inference_mode_enabled()
        assert not torch.is_autocast_enabled("cuda")
        if corrupt and calls == failing_probe:
            actual["input_features"][0, 0, 0] += 1
        compare(reference, actual)

    monkeypatch.setattr(frontend, "check_feature_equivalence", inject_drift)
    with pytest.raises(
        runtime_module.assets.NemotronModelError, match="load-time CPU/CUDA"
    ) as failure:
        runtime_module.load_nemotron_model(device="cuda:0")
    assert isinstance(failure.value.__cause__, NemotronStateError)
    assert calls == failing_probe
    assert runtime_module._RUNTIMES == {}
    corrupt = False
    runtime = runtime_module.load_nemotron_model(device="cuda:0")
    assert calls == failing_probe + 6
    assert runtime_module.load_nemotron_model(device="cuda:0") is runtime
    assert calls == failing_probe + 6
    runtime_module.release_nemotron_model_cache()
    assert runtime_module.load_nemotron_model(device="cuda:0") is not runtime
    assert calls == failing_probe + 12
