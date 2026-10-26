"""Verify lazy loading and real native processor/window behaviour without a GPU."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing import nemotron_model as runtime_module
from backend.processing.nemotron_frontend import CudaFrontend
from backend.processing.nemotron_model import NemotronRuntime
from backend.processing.nemotron_types import NemotronStateError


@pytest.fixture
def native_processor() -> Any:
    from transformers import Nemotron3DiarizationProcessor
    from transformers.models.nemotron_asr_streaming.feature_extraction_nemotron_asr_streaming import (
        NemotronAsrStreamingFeatureExtractor,
    )

    return Nemotron3DiarizationProcessor(
        NemotronAsrStreamingFeatureExtractor(feature_size=128)
    )


class NativeModelSpy:
    """A model-boundary spy; the actual native processor supplies its model kwargs."""

    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs: Any) -> Any:
        import torch
        from transformers.models.nemotron3_diarization.configuration_nemotron3_diarization import (
            Nemotron3DiarizationStreamingConfig,
        )
        from transformers.models.nemotron3_diarization.modeling_nemotron3_diarization import (
            Nemotron3DiarizationSpeakerCache,
        )

        assert torch.is_inference_mode_enabled()
        assert kwargs["input_features"].dtype == torch.float32
        self.calls.append(kwargs)
        cache = kwargs["speaker_cache"]
        if cache is None:
            assert "num_lookahead_frames" in kwargs, (
                "first EOF accidentally selected offline mode"
            )
            cache = Nemotron3DiarizationSpeakerCache(
                Nemotron3DiarizationStreamingConfig()
            )
            cache.lazy_initialization(torch.zeros(1, 1, 512))
        frames = (
            len(kwargs["input_features"][0]) - kwargs.get("num_lookahead_frames", 0) * 8
        )
        cache.num_fifo_frames += 1
        cache.fifo[0, 0, 0] = cache.num_fifo_frames
        return SimpleNamespace(logits=torch.zeros(1, frames, 8), speaker_cache=cache)


def _cpu_runtime(processor: Any) -> NemotronRuntime:
    import copy

    streaming, offline, reference = (copy.deepcopy(processor) for _ in range(3))
    # Running the CUDA adapter's arithmetic on CPU tests its semantic equivalence
    # without requiring a device. Only the public loader admits production CUDA.
    streaming.feature_extractor = CudaFrontend(
        streaming.feature_extractor, device="cpu"
    )
    return NemotronRuntime(
        NativeModelSpy(), streaming, offline, reference, "cpu", "assets", "runtime"
    )


@pytest.mark.parametrize("device", ["cpu", "mps", "cuda:abc", "cuda:-1"])
def test_cpu_and_invalid_workers_do_not_import_transformers(device: str) -> None:
    script = """
import sys
from backend.processing.nemotron_model import load_nemotron_model
from backend.utils.nemotron_model_utils import NemotronModelError
try:
    load_nemotron_model(device=sys.argv[1])
except NemotronModelError:
    pass
else:
    raise AssertionError('invalid worker accepted')
assert not any(n == 'transformers' or n.startswith('transformers.') for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, device],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_light_imports_and_release_never_import_torch_or_transformers() -> None:
    script = """
import sys
from backend.processing.live_diarization.nemotron import NemotronStream
from backend.processing.nemotron_model import release_nemotron_model_cache
release_nemotron_model_cache()
assert 'torch' not in sys.modules
assert not any(n == 'transformers' or n.startswith('transformers.') for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_cuda_unavailable_is_rejected_before_transformers_import() -> None:
    script = """
import sys
import torch
torch.cuda.is_available = lambda: False
from backend.processing.nemotron_model import load_nemotron_model
from backend.utils.nemotron_model_utils import NemotronModelError
try:
    load_nemotron_model(device='cuda')
except NemotronModelError:
    pass
else:
    raise AssertionError('unavailable CUDA accepted')
assert 'transformers' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_loader_verifies_before_build_and_once_for_shared_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import torch

    calls: list[str] = []
    resolution = SimpleNamespace(state="verified", path=Path("/local/pinned"))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        runtime_module.assets, "inspect_nemotron_model", lambda: resolution
    )
    monkeypatch.setattr(
        runtime_module.assets,
        "verify_nemotron_model",
        lambda path: calls.append("verify"),
    )
    monkeypatch.setattr(runtime_module.assets, "load_asset_manifest", lambda: {})

    def build(path: Path, device: str, manifest: dict[str, Any]) -> Any:
        assert calls[-1] == "verify"
        calls.append("build")
        return object()

    monkeypatch.setattr(runtime_module, "_build_runtime", build)
    runtime_module.release_nemotron_model_cache()
    try:
        first = runtime_module.load_nemotron_model(device="cuda:0")
        assert runtime_module.load_nemotron_model(device="cuda:0") is first
        assert calls == ["verify", "build"]
        runtime_module.release_nemotron_model_cache()
        assert runtime_module.load_nemotron_model(device="cuda:0") is not first
        assert calls == ["verify", "build", "verify", "build"]
    finally:
        runtime_module.release_nemotron_model_cache()


def test_invalid_assets_never_reach_native_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        runtime_module.assets,
        "inspect_nemotron_model",
        lambda: SimpleNamespace(state="invalid", path=None),
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid assets reached native loader")

    monkeypatch.setattr(runtime_module, "_build_runtime", forbidden)
    runtime_module.release_nemotron_model_cache()
    with pytest.raises(runtime_module.assets.NemotronModelError, match="verified"):
        runtime_module.load_nemotron_model(device="cuda:0")


def test_first_eof_explicitly_selects_streaming_with_native_processor(
    native_processor: Any,
) -> None:
    runtime = _cpu_runtime(native_processor)
    result = runtime.infer_window(
        np.ones(160, np.float32), is_first=True, is_last=True, speaker_cache=None
    )
    assert runtime.model.calls[0]["num_lookahead_frames"] == 0
    assert result.probabilities.shape == (1, 8)
    assert result.speaker_cache.fifo_length == 264
    assert result.speaker_cache.speaker_cache_update_period == 222
    assert str(result.speaker_cache.embeds.device) == "cpu"


def test_load_probes_compare_real_cpu_adapter_with_native_reference(
    native_processor: Any,
) -> None:
    import torch

    from backend.processing.nemotron_frontend import check_streaming_frontend

    runtime = _cpu_runtime(native_processor)
    probes: list[tuple[int, bool, bool]] = []

    def reference(audio: np.ndarray, **options: Any) -> Any:
        probes.append(
            (
                len(audio),
                options["is_first_audio_chunk"],
                options["is_last_audio_chunk"],
            )
        )
        return runtime.cpu_streaming_processor(audio, **options)

    with torch.autocast("cpu", dtype=torch.bfloat16):
        check_streaming_frontend(reference, runtime.streaming_processor, device="cpu")
    assert runtime.model.calls == []
    assert probes == [
        (16680, True, False),
        (17040, False, False),
        (5921, False, True),
        (560, False, True),
        (800, True, True),
        (160, True, True),
    ]


@pytest.mark.parametrize("samples", [0, 1, 159])
def test_no_feature_eof_returns_no_cache_without_forward(
    native_processor: Any, samples: int
) -> None:
    runtime = _cpu_runtime(native_processor)
    result = runtime.infer_window(
        np.ones(samples, np.float32), is_first=True, is_last=True, speaker_cache=None
    )
    assert result.probabilities.shape == (0, 8)
    assert result.speaker_cache is None
    assert runtime.model.calls == []


def test_first_later_and_eof_windows_use_independent_processors(
    native_processor: Any,
) -> None:
    runtime = _cpu_runtime(native_processor)
    first = runtime.infer_window(
        np.ones(16680, np.float32), is_first=True, is_last=False, speaker_cache=None
    )
    later = runtime.infer_window(
        np.ones(17040, np.float32),
        is_first=False,
        is_last=False,
        speaker_cache=first.speaker_cache,
    )
    last = runtime.infer_window(
        np.ones(5920, np.float32),
        is_first=False,
        is_last=True,
        speaker_cache=later.speaker_cache,
    )
    assert [
        len(first.probabilities),
        len(later.probabilities),
        len(last.probabilities),
    ] == [72, 72, 35]
    assert [c.get("num_lookahead_frames") for c in runtime.model.calls] == [4, 4, None]
    assert runtime.streaming_processor is not runtime.offline_processor
    assert (
        runtime.offline_processor.feature_extractor
        is not runtime.cpu_streaming_processor.feature_extractor
    )
    assert not isinstance(runtime.offline_processor.feature_extractor, CudaFrontend)


@pytest.mark.parametrize("samples", [800, 64001])
def test_serving_never_calls_cpu_reference_processor(
    native_processor: Any, samples: int
) -> None:
    from dataclasses import replace

    from backend.processing.live_diarization.nemotron import NemotronStream

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("serving called the CPU reference processor")

    runtime = replace(_cpu_runtime(native_processor), cpu_streaming_processor=forbidden)
    stream = NemotronStream(runtime)
    audio = np.zeros(samples, np.float32)
    blocks = stream.feed(audio, 0)
    blocks.extend(stream.flush(audio_end_n=len(audio)))
    assert sum(len(block.probabilities) for block in blocks) == samples // 160
    assert len(runtime.model.calls) == (1 if samples == 800 else 6)


def test_wrong_cache_or_window_is_rejected_before_model_call(
    native_processor: Any,
) -> None:
    runtime = _cpu_runtime(native_processor)
    with pytest.raises(NemotronStateError, match="sample count"):
        runtime.infer_window(
            np.ones(16640, np.float32), is_first=True, is_last=False, speaker_cache=None
        )
    with pytest.raises(NemotronStateError, match="prior cache"):
        runtime.infer_window(
            np.ones(17040, np.float32),
            is_first=False,
            is_last=False,
            speaker_cache=None,
        )
    assert runtime.model.calls == []


def test_shared_model_serial_interleaving_keeps_two_native_caches_independent(
    native_processor: Any,
) -> None:
    from backend.processing.live_diarization.nemotron import NemotronStream

    runtime = _cpu_runtime(native_processor)
    first, second = NemotronStream(runtime), NemotronStream(runtime)
    audio = np.zeros(32000, np.float32)
    first.feed(audio, 0)
    second.feed(audio, 0)
    assert first.speaker_cache is not second.speaker_cache
    assert first.speaker_cache.fifo[0, 0, 0] == 2
    assert second.speaker_cache.fifo[0, 0, 0] == 2
    first.feed(audio, 32000)
    assert first.speaker_cache.fifo[0, 0, 0] == 5
    assert second.speaker_cache.fifo[0, 0, 0] == 2


def test_partial_cache_transfer_failure_runs_cpu_cleanup(
    native_processor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _cpu_runtime(native_processor)
    first = runtime.infer_window(
        np.zeros(16680, np.float32),
        is_first=True,
        is_last=False,
        speaker_cache=None,
    )
    transfers: list[tuple[Any, str]] = []

    def fail_transfer(cache: Any, device: str) -> None:
        transfers.append((cache, device))
        if len(transfers) == 1:
            raise RuntimeError("fake OOM after a partial cache transfer")

    monkeypatch.setattr(NemotronRuntime, "_move_cache", staticmethod(fail_transfer))
    with pytest.raises(RuntimeError, match="partial cache transfer"):
        runtime.infer_window(
            np.zeros(17040, np.float32),
            is_first=False,
            is_last=False,
            speaker_cache=first.speaker_cache,
        )
    assert transfers == [
        (first.speaker_cache, runtime.device),
        (first.speaker_cache, "cpu"),
    ]
    assert len(runtime.model.calls) == 1
