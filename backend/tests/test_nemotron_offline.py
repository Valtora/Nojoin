"""Offline native processor/model contracts on CPU, without weights or downloads."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing.nemotron_model import NemotronRuntime
from backend.processing.nemotron_types import NemotronStateError


@pytest.fixture
def offline_runtime() -> NemotronRuntime:
    import torch
    from transformers import Nemotron3DiarizationProcessor
    from transformers.models.nemotron_asr_streaming.feature_extraction_nemotron_asr_streaming import (
        NemotronAsrStreamingFeatureExtractor,
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("offline inference touched a streaming processor")

    def model(**inputs: Any) -> Any:
        assert torch.is_inference_mode_enabled()
        assert not torch.is_autocast_enabled("cpu")
        assert set(inputs) == {"input_features", "attention_mask"}
        features = inputs["input_features"]
        assert features.dtype == torch.float32
        frames = features.shape[1]
        logits = torch.zeros(1, frames, 8, dtype=torch.float32)
        logits[0, -1, 7] = 1
        return SimpleNamespace(logits=logits, speaker_cache=None)

    processor = Nemotron3DiarizationProcessor(
        NemotronAsrStreamingFeatureExtractor(feature_size=128)
    )
    return NemotronRuntime(
        model, forbidden, processor, forbidden, "cpu", "assets", "runtime"
    )


@pytest.mark.parametrize("samples", [1, 159, 160, 161, 800, 40000])
def test_offline_preserves_physical_final_frame_and_uses_cpu_frontend(
    offline_runtime: NemotronRuntime, samples: int
) -> None:
    import torch

    audio = np.ones(samples, np.float32)
    with torch.autocast("cpu", dtype=torch.bfloat16):
        result = offline_runtime.infer_offline(audio)
    assert result.shape == (samples // 160 + 1, 8)
    assert result.dtype == np.float32
    assert result.flags.owndata and result.flags.c_contiguous
    assert result[-1, 7] > 0.5
    np.testing.assert_array_equal(result[:-1], 0.5)


@pytest.mark.parametrize(
    "audio",
    [
        np.empty(0, np.float32),
        np.zeros(800, np.float64),
        np.zeros((800, 1), np.float32),
        np.array([np.nan], np.float32),
    ],
)
def test_offline_rejects_bad_audio(
    offline_runtime: NemotronRuntime, audio: np.ndarray
) -> None:
    with pytest.raises(NemotronStateError):
        offline_runtime.infer_offline(audio)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "rank",
        "batch",
        "frames",
        "channels",
        "half",
        "nan",
        "inf",
        "negative_inf",
        "cache",
    ],
)
def test_offline_rejects_malformed_raw_model_output(
    offline_runtime: NemotronRuntime, kind: str
) -> None:
    import torch

    def model(**inputs: Any) -> Any:
        logits = torch.zeros(1, 6, 8)
        changes = {
            "rank": torch.zeros(6, 8),
            "batch": torch.zeros(2, 6, 8),
            "frames": torch.zeros(1, 5, 8),
            "channels": torch.zeros(1, 6, 7),
            "half": logits.half(),
            "nan": torch.full_like(logits, float("nan")),
            "inf": torch.full_like(logits, float("inf")),
            "negative_inf": torch.full_like(logits, -float("inf")),
        }
        if kind == "missing":
            return None
        return SimpleNamespace(
            logits=changes.get(kind, logits),
            speaker_cache=object() if kind == "cache" else None,
        )

    object.__setattr__(offline_runtime, "model", model)
    with pytest.raises(NemotronStateError, match="offline logits"):
        offline_runtime.infer_offline(np.zeros(800, np.float32))


@pytest.mark.parametrize("kind", ["features", "mask", "mask_count", "lookahead"])
def test_offline_rejects_changed_processor_geometry(
    offline_runtime: NemotronRuntime, kind: str
) -> None:
    original = offline_runtime.offline_processor

    def processor(audio: np.ndarray, **options: Any) -> Any:
        result = original(audio, **options)
        if kind == "features":
            result["input_features"] = result["input_features"][:, :-1]
        elif kind == "mask":
            result["attention_mask"] = result["attention_mask"][:, :-1]
        elif kind == "mask_count":
            result["attention_mask"][:] = 1
        else:
            result["num_lookahead_frames"] = 0
        return result

    object.__setattr__(offline_runtime, "offline_processor", processor)
    with pytest.raises(NemotronStateError, match="feature geometry"):
        offline_runtime.infer_offline(np.zeros(800, np.float32))


def test_offline_processor_and_forward_share_serial_lock(
    offline_runtime: NemotronRuntime,
) -> None:
    original = offline_runtime.offline_processor
    original_model = offline_runtime.model
    locked: list[bool] = []

    class Lock:
        def __enter__(self) -> None:
            locked.append(True)

        def __exit__(self, *args: Any) -> None:
            locked.pop()

    def processor(audio: np.ndarray, **options: Any) -> Any:
        assert locked == [True]
        return original(audio, **options)

    def model(**inputs: Any) -> Any:
        assert locked == [True]
        return original_model(**inputs)

    object.__setattr__(offline_runtime, "_forward_lock", Lock())
    object.__setattr__(offline_runtime, "offline_processor", processor)
    object.__setattr__(offline_runtime, "model", model)
    offline_runtime.infer_offline(np.zeros(800, np.float32))
    assert locked == []
