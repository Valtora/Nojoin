"""Verified local-only native Nemotron loading and serial fp32 window inference.

Nothing imports Transformers until a CUDA worker explicitly loads the runtime.
This module does not select an engine, prepare assets or wire live/final callers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from backend.processing.nemotron_frontend import CudaFrontend, check_streaming_frontend
from backend.processing.nemotron_types import (
    NEMOTRON_TRANSFORMERS_VERSION,
    NativeWindowResult,
    NemotronGeometry,
    NemotronStateError,
    validate_audio,
    validate_cache,
    validate_probabilities,
)
from backend.utils import nemotron_model_utils as assets

_RUNTIMES: dict[str, NemotronRuntime] = {}
_LOAD_LOCK = threading.RLock()
_PRECISION_LOCK = threading.RLock()


@contextmanager
def _nemotron_precision() -> Iterator[None]:
    """Keep evaluated TF32 settings local to serial Nemotron operations."""
    import torch

    with _PRECISION_LOCK:
        previous_matmul = torch.backends.cuda.matmul.allow_tf32
        previous_cudnn = torch.backends.cudnn.allow_tf32
        previous_cuda_policy = torch.backends.cuda.matmul.fp32_precision
        previous_conv_policy = torch.backends.cudnn.conv.fp32_precision
        previous_rnn_policy = torch.backends.cudnn.rnn.fp32_precision
        try:
            previous_mode = torch.get_float32_matmul_precision()
        except RuntimeError:
            # Frameworks can leave mixed precision APIs with readable booleans.
            previous_mode = None
        previous_cpu_policy = torch.backends.mkldnn.matmul.fp32_precision
        try:
            torch.backends.cuda.matmul.allow_tf32 = False
            torch.backends.cudnn.allow_tf32 = True
            yield
        finally:
            torch.backends.cuda.matmul.allow_tf32 = previous_matmul
            torch.backends.cudnn.allow_tf32 = previous_cudnn
            if previous_mode == "medium":
                # Boolean restoration collapses medium to high; the mode setter
                # also changes CPU matmul policy, which must remain untouched.
                torch.set_float32_matmul_precision(previous_mode)
                torch.backends.mkldnn.matmul.fp32_precision = previous_cpu_policy
            torch.backends.cuda.matmul.fp32_precision = previous_cuda_policy
            torch.backends.cudnn.conv.fp32_precision = previous_conv_policy
            torch.backends.cudnn.rnn.fp32_precision = previous_rnn_policy


def _require_attributes(value: Any, expected: dict[str, Any], context: str) -> None:
    for name, wanted in expected.items():
        if getattr(value, name, None) != wanted:
            raise assets.NemotronModelError(
                f"Nemotron {context}.{name} differs from the evaluated geometry"
            )


def _check_processor(processor: Any) -> None:
    geometry = NemotronGeometry()
    _require_attributes(
        processor,
        {
            "subsampling_factor": 8,
            "streaming_mode": "low_latency",
            "num_samples_first_audio_chunk": geometry.num_samples_first_audio_chunk,
            "num_samples_per_audio_chunk": geometry.num_samples_per_audio_chunk,
            "num_mel_frames_per_step": 72,
            "num_mel_frames_per_audio_chunk": 104,
        },
        "processor",
    )
    _require_attributes(
        processor.feature_extractor,
        {
            "sampling_rate": 16000,
            "hop_length": 160,
            "n_fft": 512,
            "win_length": 400,
            "feature_size": 128,
            "preemphasis": 0.97,
            "padding_value": 0.0,
            "padding_side": "right",
        },
        "feature extractor",
    )


def _check_model(model: Any, torch: Any) -> None:
    _require_attributes(
        model.config,
        {
            "model_type": "nemotron3_diarization",
            "chunk_length": 340,
            "chunk_right_context": 40,
            "fifo_length": 40,
            "speaker_cache_update_period": 300,
            "_attn_implementation": "sdpa",
        },
        "config",
    )
    _require_attributes(
        model.config.audio_config,
        {"hidden_size": 512, "num_mel_bins": 128, "subsampling_factor": 8},
        "audio",
    )
    _require_attributes(
        model.config.head_config, {"num_speakers": 8, "subsampling_factor": 8}, "head"
    )
    _require_attributes(
        model.config.streaming_config,
        {
            "fifo_length": 264,
            "speaker_cache_length": 264,
            "speaker_cache_update_period": 222,
            "num_speakers": 8,
            "subsampling_factor": 8,
            "speaker_cache_silence_frames_per_speaker": 1,
            "prediction_score_threshold": 0.25,
            "latest_frames_score_boost": 0.05,
            "min_positive_scores_rate": 0.5,
            "strong_boost_rate": 0.75,
            "weak_boost_rate": 1.5,
        },
        "streaming config",
    )
    if any(parameter.dtype != torch.float32 for parameter in model.parameters()):
        raise assets.NemotronModelError(
            "Nemotron model contains parameters outside fp32"
        )


def _fingerprint(asset_digest: str, torch_version: str) -> str:
    data = {
        "assets": asset_digest,
        "geometry": asdict(NemotronGeometry()),
        "transformers": NEMOTRON_TRANSFORMERS_VERSION,
        "torch": torch_version,
        "precision": "float32",
        "attention": "sdpa",
        "route_probabilities": "float16",
        "tf32_matmul": False,
        "tf32_cudnn": True,
        "frontend": "cuda-with-cpu-equivalence-v1",
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class NemotronRuntime:
    """One process-local model and independent processors, with no per-meeting cache."""

    model: Any
    streaming_processor: Any
    offline_processor: Any
    cpu_streaming_processor: Any
    device: str
    asset_fingerprint: str
    runtime_fingerprint: str
    geometry: NemotronGeometry = field(default_factory=NemotronGeometry)
    _forward_lock: Any = field(default_factory=threading.RLock, init=False, repr=False)

    def infer_offline(self, audio: np.ndarray) -> np.ndarray:
        """Run the studied full-waveform CPU frontend and offline model geometry.

        Preserve the physical final STFT frame, including its masked feature
        vector: the cached study thresholds every returned offline logit.
        """
        import torch

        validate_audio(audio)
        if not len(audio):
            raise NemotronStateError("Nemotron offline audio must not be empty")
        frames = len(audio) // self.geometry.hop_length + 1
        with (
            self._forward_lock,
            _nemotron_precision(),
            torch.inference_mode(),
            torch.autocast(device_type=self.device.split(":")[0], enabled=False),
        ):
            inputs = self.offline_processor(
                audio, sampling_rate=16000, is_streaming=False, return_tensors="pt"
            )
            if (
                set(inputs) != {"input_features", "attention_mask"}
                or inputs["input_features"].shape != (1, frames, 128)
                or inputs["attention_mask"].shape != (1, frames)
                or int(inputs["attention_mask"].sum()) != frames - 1
            ):
                raise NemotronStateError("Nemotron offline feature geometry differs")
            output = self.model(**inputs.to(self.device))
            logits = getattr(output, "logits", None)
            if (
                not isinstance(logits, torch.Tensor)
                or logits.dtype != torch.float32
                or logits.shape != (1, frames, 8)
                or not torch.isfinite(logits).all()
                or getattr(output, "speaker_cache", None) is not None
            ):
                raise NemotronStateError("Nemotron returned malformed offline logits")
            probabilities = logits.sigmoid()[0].cpu().numpy().copy()
        validate_probabilities(probabilities, frames)
        return probabilities

    def infer_window(
        self,
        audio_window: np.ndarray,
        *,
        is_first: bool,
        is_last: bool,
        speaker_cache: Any | None,
    ) -> NativeWindowResult:
        """Infer with the accepted frontend; return new fp32 frames and CPU state."""
        import torch

        validate_audio(audio_window)
        if type(is_first) is not bool or type(is_last) is not bool:
            raise NemotronStateError("Nemotron first/last flags must be booleans")
        if is_first != (speaker_cache is None):
            raise NemotronStateError(
                "Nemotron first-window flag and prior cache disagree"
            )
        frames = self.geometry.output_frames(
            len(audio_window), first=is_first, last=is_last
        )
        if not is_last:
            expected = (
                self.geometry.num_samples_first_audio_chunk
                if is_first
                else self.geometry.num_samples_per_audio_chunk
            )
            if len(audio_window) != expected:
                raise NemotronStateError(
                    "Nemotron regular window has the wrong sample count"
                )
        if speaker_cache is not None:
            self._validate_native_cache(speaker_cache)
        if frames == 0:
            return NativeWindowResult(np.empty((0, 8), np.float32), speaker_cache)
        with (
            self._forward_lock,
            _nemotron_precision(),
            torch.inference_mode(),
            torch.autocast(device_type=self.device.split(":")[0], enabled=False),
        ):
            return self._forward(
                audio_window, first=is_first, last=is_last, cache=speaker_cache
            )

    def _forward(
        self, audio: np.ndarray, *, first: bool, last: bool, cache: Any | None
    ) -> NativeWindowResult:
        options = dict(
            sampling_rate=16000,
            is_streaming=True,
            is_first_audio_chunk=first,
            is_last_audio_chunk=last,
            return_tensors="pt",
        )
        inputs = self.streaming_processor(audio, **options)
        inputs = inputs.to(self.device)
        if first and last:
            # EOF omits lookahead. Without a cache, zero explicitly selects
            # streaming instead of the native model's offline 40/300 policy.
            inputs["num_lookahead_frames"] = 0
        output: Any = None
        try:
            self._move_cache(cache, self.device)
            output = self.model(**inputs, speaker_cache=cache)
            probabilities = output.logits.sigmoid()[0].float().cpu().numpy().copy()
        finally:
            self._move_cache(cache, "cpu")
            if output is not None:
                self._move_cache(output.speaker_cache, "cpu")
        frames = self.geometry.output_frames(len(audio), first=first, last=last)
        validate_probabilities(probabilities, frames)
        self._validate_native_cache(output.speaker_cache)
        return NativeWindowResult(probabilities, output.speaker_cache)

    def _validate_native_cache(self, cache: Any) -> None:
        from transformers.models.nemotron3_diarization.modeling_nemotron3_diarization import (
            Nemotron3DiarizationSpeakerCache,
        )

        if type(cache) is not Nemotron3DiarizationSpeakerCache:
            raise NemotronStateError(
                "Nemotron returned an unexpected native cache class"
            )
        validate_cache(cache, self.geometry)

    @staticmethod
    def _move_cache(cache: Any | None, device: str) -> None:
        if cache is not None:
            for name in ("embeds", "probs", "fifo"):
                value = getattr(cache, name)
                if value is not None:
                    setattr(cache, name, value.to(device))


def _build_processors(path: Path) -> list[Any]:
    """Construct native classes from verified JSON without sidecar discovery."""
    from transformers.models.nemotron3_diarization.processing_nemotron3_diarization import (
        Nemotron3DiarizationProcessor,
    )
    from transformers.models.nemotron_asr_streaming.feature_extraction_nemotron_asr_streaming import (
        NemotronAsrStreamingFeatureExtractor,
    )

    config = assets.read_json(path / "processor_config.json")
    processors = [
        Nemotron3DiarizationProcessor(
            feature_extractor=NemotronAsrStreamingFeatureExtractor(
                **config["feature_extractor"]
            ),
            subsampling_factor=config["subsampling_factor"],
            streaming_modes=copy.deepcopy(config["streaming_modes"]),
            streaming_mode=config["streaming_mode"],
        )
        for _ in range(3)
    ]
    for processor in processors:
        if (
            type(processor) is not Nemotron3DiarizationProcessor
            or type(processor.feature_extractor)
            is not NemotronAsrStreamingFeatureExtractor
        ):
            raise assets.NemotronModelError(
                "Nemotron loaded unexpected processor/feature extractor classes"
            )
        _check_processor(processor)
    return processors


def _build_runtime(
    path: Path, device: str, manifest: dict[str, Any]
) -> NemotronRuntime:
    import torch
    import transformers
    from transformers.models.nemotron3_diarization.modeling_nemotron3_diarization import (
        Nemotron3DiarizationForAudioFrameClassification,
    )

    if (
        transformers.__version__ != NEMOTRON_TRANSFORMERS_VERSION
        or manifest["transformers_version"] != transformers.__version__
    ):
        raise assets.NemotronModelError(
            "Nemotron requires the exact evaluated Transformers version"
        )
    processors = _build_processors(path)
    options = dict(local_files_only=True, trust_remote_code=False)
    with _nemotron_precision():
        model = (
            Nemotron3DiarizationForAudioFrameClassification.from_pretrained(
                str(path),
                **options,
                use_safetensors=True,
                dtype=torch.float32,
                attn_implementation="sdpa",
            )
            .eval()
            .to(device)
        )
        if type(model) is not Nemotron3DiarizationForAudioFrameClassification:
            raise assets.NemotronModelError("Nemotron loaded an unexpected model class")
        _check_model(model, torch)
        processors[0].feature_extractor = CudaFrontend(
            processors[0].feature_extractor, device=device
        )
        try:
            check_streaming_frontend(processors[2], processors[0], device=device)
        except NemotronStateError as exc:
            raise assets.NemotronModelError(
                "Nemotron load-time CPU/CUDA frontend acceptance failed"
            ) from exc
    digest = assets.manifest_sha256()
    return NemotronRuntime(
        model,
        processors[0],
        processors[1],
        processors[2],
        device,
        digest,
        _fingerprint(digest, torch.__version__),
    )


def load_nemotron_model(*, device: str) -> NemotronRuntime:
    """Load only a verified local pinned snapshot, after rejecting CPU workers."""
    if not isinstance(device, str) or re.fullmatch(r"cuda(?::[0-9]+)?", device) is None:
        raise assets.NemotronModelError(
            "Nemotron native inference requires a CUDA worker"
        )
    import torch

    if not torch.cuda.is_available():
        raise assets.NemotronModelError(
            "Nemotron native inference requires available CUDA"
        )
    with _LOAD_LOCK:
        if device not in _RUNTIMES:
            resolution = assets.inspect_nemotron_model()
            if resolution.state != "verified" or resolution.path is None:
                raise assets.NemotronModelError(
                    "Nemotron needs prepared verified local model assets"
                )
            assets.verify_nemotron_model(resolution.path)
            _RUNTIMES[device] = _build_runtime(
                resolution.path, device, assets.load_asset_manifest()
            )
        return _RUNTIMES[device]


def release_nemotron_model_cache() -> None:
    """Drop this process's shared runtime references without importing ML libraries."""
    with _LOAD_LOCK:
        _RUNTIMES.clear()
