"""Nemotron precision is established in-call and restored on every exit path."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing import nemotron_model as runtime_module
from backend.processing.nemotron_model import NemotronRuntime
from backend.processing.nemotron_types import NEMOTRON_TRANSFORMERS_VERSION
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.tests.test_nemotron_loader import constructor_spies as constructor_spies

PRIOR_FLAGS = [(False, False), (False, True), (True, False), (True, True)]


def _flags() -> tuple[bool, bool]:
    import torch

    return torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32


def _set_flags(monkeypatch: pytest.MonkeyPatch, prior: tuple[bool, bool]) -> None:
    import torch

    monkeypatch.setattr(torch.backends.cuda.matmul, "allow_tf32", prior[0])
    monkeypatch.setattr(torch.backends.cudnn, "allow_tf32", prior[1])


@pytest.mark.parametrize("prior", PRIOR_FLAGS)
@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("mode", ["window", "offline"])
def test_inference_precision_is_local_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    prior: tuple[bool, bool],
    failure: type[BaseException] | None,
    mode: str,
) -> None:
    import torch
    from transformers.feature_extraction_utils import BatchFeature

    _set_flags(monkeypatch, prior)
    calls: list[str] = []

    def processor(audio: np.ndarray, **options: Any) -> Any:
        assert _flags() == (False, True)
        calls.append("processor")
        frames = len(audio) // 160 + (not options["is_streaming"])
        mask = torch.ones((1, frames), dtype=torch.bool)
        if not options["is_streaming"]:
            mask[0, -1] = False
        return BatchFeature(
            {"input_features": torch.zeros(1, frames, 128), "attention_mask": mask}
        )

    def model(**inputs: Any) -> Any:
        assert _flags() == (False, True)
        calls.append("model")
        if failure is not None:
            raise failure("precision probe")
        return SimpleNamespace(
            logits=torch.zeros(1, inputs["input_features"].shape[1], 8),
            speaker_cache=None,
        )

    monkeypatch.setattr(NemotronRuntime, "_validate_native_cache", lambda *args: None)
    runtime = NemotronRuntime(
        model, processor, processor, processor, "cpu", "assets", "runtime"
    )
    expectation = nullcontext() if failure is None else pytest.raises(failure)
    with expectation:
        if mode == "offline":
            runtime.infer_offline(np.zeros(800, np.float32))
        else:
            runtime.infer_window(
                np.zeros(800, np.float32),
                is_first=True,
                is_last=True,
                speaker_cache=None,
            )
    assert calls == ["processor", "model"]
    assert _flags() == prior


def _policy_snapshot() -> tuple[Any, ...]:
    import torch

    try:
        mode = torch.get_float32_matmul_precision()
    except RuntimeError:
        mode = None
    return (
        mode,
        *_flags(),
        torch.backends.mkldnn.matmul.fp32_precision,
        torch.backends.cuda.matmul.fp32_precision,
        torch.backends.cudnn.conv.fp32_precision,
        torch.backends.cudnn.rnn.fp32_precision,
    )


@pytest.fixture
def precision_policy_state() -> Iterator[None]:
    import torch

    previous = _policy_snapshot()
    parent = torch.backends.fp32_precision
    cudnn_parent = torch.backends.cudnn.fp32_precision
    try:
        torch.backends.fp32_precision = "none"
        torch.backends.cudnn.fp32_precision = "none"
        torch.set_float32_matmul_precision("highest")
        torch.backends.cudnn.allow_tf32 = True
        yield
    finally:
        torch.backends.fp32_precision = parent
        torch.backends.cudnn.fp32_precision = cudnn_parent
        torch.backends.cuda.matmul.allow_tf32 = previous[1]
        torch.backends.cudnn.allow_tf32 = previous[2]
        if previous[0] == "medium":
            torch.set_float32_matmul_precision(previous[0])
        torch.backends.mkldnn.matmul.fp32_precision = previous[3]
        torch.backends.cuda.matmul.fp32_precision = previous[4]
        torch.backends.cudnn.conv.fp32_precision = previous[5]
        torch.backends.cudnn.rnn.fp32_precision = previous[6]


@pytest.mark.parametrize("mode", ["highest", "high", "medium"])
@pytest.mark.parametrize("pyannote", [False, True])
@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
def test_scope_preserves_richer_policy_and_mixed_framework_state(
    precision_policy_state: None,
    mode: str,
    pyannote: bool,
    failure: type[BaseException] | None,
) -> None:
    import torch

    torch.set_float32_matmul_precision(mode)
    if pyannote:
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    previous = _policy_snapshot()
    with nullcontext() if failure is None else pytest.raises(failure):
        with runtime_module._nemotron_precision():
            assert _flags() == (False, True)
            if failure is not None:
                raise failure("precision policy probe")
    assert _policy_snapshot() == previous


def test_scope_preserves_explicit_cudnn_policy_under_parent_tf32(
    precision_policy_state: None,
) -> None:
    import torch

    torch.backends.cudnn.allow_tf32 = False
    torch.backends.fp32_precision = "tf32"
    torch.backends.cudnn.conv.fp32_precision = "ieee"
    torch.backends.cudnn.rnn.fp32_precision = "ieee"
    previous = _policy_snapshot()
    with runtime_module._nemotron_precision():
        assert _flags() == (False, True)
    assert _policy_snapshot() == previous


def test_scope_preserves_inheriting_cuda_matmul_policy(
    precision_policy_state: None,
) -> None:
    import torch

    torch.backends.cuda.matmul.fp32_precision = "none"
    previous = _policy_snapshot()
    with runtime_module._nemotron_precision():
        assert _flags() == (False, True)
    assert _policy_snapshot() == previous


@pytest.mark.parametrize("prior", PRIOR_FLAGS)
@pytest.mark.parametrize("stage", ["success", "constructor", "frontend"])
def test_loading_precision_covers_constructor_and_acceptance_then_restores(
    constructor_spies: Any,
    monkeypatch: pytest.MonkeyPatch,
    prior: tuple[bool, bool],
    stage: str,
) -> None:
    _set_flags(monkeypatch, prior)
    calls: list[str] = []
    original = constructor_spies.model_class.from_pretrained

    def constructor(*args: Any, **kwargs: Any) -> Any:
        assert _flags() == (False, True)
        calls.append("constructor")
        if stage == "constructor":
            raise RuntimeError("constructor precision probe")
        return original(*args, **kwargs)

    def acceptance(*args: Any, **kwargs: Any) -> None:
        assert _flags() == (False, True)
        calls.append("frontend")
        if stage == "frontend":
            raise RuntimeError("frontend precision probe")

    monkeypatch.setattr(
        constructor_spies.model_class, "from_pretrained", staticmethod(constructor)
    )
    monkeypatch.setattr(runtime_module, "check_streaming_frontend", acceptance)
    expectation = nullcontext() if stage == "success" else pytest.raises(RuntimeError)
    with expectation:
        runtime_module._build_runtime(
            constructor_spies.path,
            "cuda:0",
            {"transformers_version": NEMOTRON_TRANSFORMERS_VERSION},
        )
    assert calls == (
        ["constructor"] if stage == "constructor" else ["constructor", "frontend"]
    )
    assert _flags() == prior
