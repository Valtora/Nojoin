"""CUDA waveform preparation around the pinned upstream Torch STFT/mel code.

Imports remain lazy so importing the runtime never imports Transformers.
The offline processor and the CPU streaming reference own separate extractors.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from backend.processing.nemotron_types import NemotronGeometry, NemotronStateError


class CudaFrontend:
    """Move pre-emphasis, EOF padding, filters and masks to the chosen CUDA device."""

    def __init__(self, base: Any, *, device: str) -> None:
        self.base = base
        self.device = device
        self.base.mel_filters = base.mel_filters.to(device)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def __call__(
        self,
        raw_speech: list[np.ndarray],
        sampling_rate: int,
        center: bool = True,
        is_last_audio_chunk: bool = False,
        **kwargs: Any,
    ) -> Any:
        import torch
        from transformers.feature_extraction_utils import BatchFeature

        if sampling_rate != 16000 or len(raw_speech) != 1:
            raise NemotronStateError(
                "Nemotron CUDA frontend requires one mono 16000 Hz waveform"
            )
        waveform = torch.as_tensor(
            np.stack(raw_speech), dtype=torch.float32, device=self.device
        )
        length = waveform.shape[1]
        if self.base.preemphasis is not None:
            waveform = torch.cat(
                (
                    waveform[:, :1],
                    waveform[:, 1:] - self.base.preemphasis * waveform[:, :-1],
                ),
                dim=1,
            )
        if is_last_audio_chunk and not center:
            padding = self.base.n_fft // 2 - self.base.hop_length
            waveform = torch.nn.functional.pad(waveform, (0, padding))
            length += padding
        features = self.base._torch_extract_fbank_features(
            waveform, device=self.device, center=center
        )
        valid = length // self.base.hop_length
        if not center:
            valid = (length - self.base.n_fft) // self.base.hop_length + 1
        mask = torch.arange(features.shape[1], device=self.device)[None, :] < valid
        features *= mask.unsqueeze(-1)
        return BatchFeature(
            {"input_features": features, "attention_mask": mask}, tensor_type="pt"
        )


def check_feature_equivalence(reference: Any, actual: Any) -> None:
    """Reject feature/mask drift at load acceptance and in fixture regression tests."""
    import torch

    expected_features = reference["input_features"]
    actual_features = actual["input_features"].cpu()
    if expected_features.shape != actual_features.shape:
        raise NemotronStateError("Nemotron CPU/CUDA feature shapes differ")
    if not torch.equal(reference["attention_mask"], actual["attention_mask"].cpu()):
        raise NemotronStateError("Nemotron CPU/CUDA attention masks differ")
    if not torch.allclose(expected_features, actual_features, atol=2e-4, rtol=1e-5):
        raise NemotronStateError(
            "Nemotron CPU/CUDA features exceed atol=2e-4, rtol=1e-5"
        )


def check_streaming_frontend(reference: Any, actual: Any, *, device: str) -> None:
    """Accept one loaded frontend on fixed first, regular and both EOF paths.

    Local deterministic noise exercises nonzero features without fixture I/O or
    model/cache mutation. Include unaligned EOF and the shortest first EOF that
    emits a frame. This bounded check never runs on serving windows.
    """
    import torch

    geometry = NemotronGeometry()
    windows = (
        (geometry.num_samples_first_audio_chunk, True, False),
        (geometry.num_samples_per_audio_chunk, False, False),
        (5921, False, True),
        (560, False, True),
        (800, True, True),
        (160, True, True),
    )
    audio = np.random.default_rng(710).standard_normal(
        geometry.num_samples_per_audio_chunk
    ).astype(np.float32) * np.float32(0.1)
    with (
        torch.inference_mode(),
        torch.autocast(device_type=device.split(":")[0], enabled=False),
    ):
        for samples, first, last in windows:
            options = dict(
                sampling_rate=16000,
                is_streaming=True,
                is_first_audio_chunk=first,
                is_last_audio_chunk=last,
                return_tensors="pt",
            )
            check_feature_equivalence(
                reference(audio[:samples], **options),
                actual(audio[:samples], **options),
            )
