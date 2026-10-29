"""Native construction and strict checkpoint validation without a real model."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing import forced_aligner_model as runtime
from backend.processing import forced_aligner_native as native
from backend.utils.forced_aligner_manifest import ForcedAlignerError


@pytest.fixture
def constructors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import torch
    from safetensors.torch import save_file

    raw = {
        "thinker_config": {
            "audio_config": {"d_model": 2},
            "text_config": {"hidden_size": 3},
            "classify_num": 5000,
            "audio_token_id": 10,
        },
        "timestamp_token_id": 11,
        "timestamp_segment_time": 80,
    }
    (tmp_path / "config.json").write_text(json.dumps(raw))
    save_file(
        {"thinker.model.weight": torch.zeros(2, 3)}, str(tmp_path / "model.safetensors")
    )
    state = SimpleNamespace(diagnostics={}, calls=[], moved=[], dtype=torch.float32)

    class Model:
        def __init__(self, config: Any) -> None:
            self.config = config

        def state_dict(self) -> dict[str, Any]:
            return {"model.language_model.weight": torch.empty(2, 3)}

        @classmethod
        def from_pretrained(cls, path: str, **kwargs: Any) -> tuple[Any, dict]:
            state.calls.append((path, kwargs))
            state.dtype = kwargs["dtype"]
            return cls(kwargs["config"]), state.diagnostics

        def parameters(self) -> list[Any]:
            return [torch.zeros(1, dtype=state.dtype)]

        def eval(self) -> Any:
            return self

        def to(self, device: str) -> Any:
            state.moved.append(device)
            return self

    # Patch the import boundary: Transformers' lazy module can replace itself
    # during its first concrete import and discard patches on the old object.
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            __version__=native.TRANSFORMERS_VERSION,
            Qwen3ASRConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            Qwen3ASRForTokenClassification=Model,
        ),
    )
    return SimpleNamespace(path=tmp_path, state=state, model_class=Model)


def test_native_mapping_config_and_local_strict_load(
    constructors: Any,
) -> None:
    model = native.load_native_model(constructors.path)
    _path, options = constructors.state.calls[0]
    assert options["local_files_only"] and options["use_safetensors"]
    assert options["trust_remote_code"] is False
    assert options["output_loading_info"] is True
    assert options["key_mapping"] is native.KEY_MAPPING
    assert options["attn_implementation"] == "sdpa"
    assert str(options["dtype"]) == "torch.float32"
    assert constructors.state.moved == ["cpu"]
    assert model.config.num_labels == 5000
    assert model.config.audio_config == {
        "d_model": 2,
        "model_type": "qwen3_asr_encoder",
    }
    assert model.config.timestamp_segment_time == 80
    assert model.config.token_classification_bias is False


@pytest.mark.parametrize(
    "diagnostic", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"]
)
def test_nonempty_loading_diagnostics_refused_before_transfer(
    constructors: Any, diagnostic: str
) -> None:
    constructors.state.diagnostics = {diagnostic: ["broken"]}
    with pytest.raises(ForcedAlignerError, match="diagnostics"):
        native.load_native_model(constructors.path)
    assert constructors.state.moved == []


@pytest.mark.parametrize(
    "key,shape",
    [
        ("thinker.model.weight", (3, 2)),
        ("thinker.model.extra", (2, 3)),
        ("unmapped.weight", (2, 3)),
    ],
)
def test_bad_key_or_shape_fails_before_loader(
    constructors: Any, key: str, shape: tuple[int, int]
) -> None:
    import torch
    from safetensors.torch import save_file

    save_file({key: torch.zeros(shape)}, str(constructors.path / "model.safetensors"))
    with pytest.raises(ForcedAlignerError):
        native.load_native_model(constructors.path)
    assert constructors.state.calls == []


def test_projector_prefixes_win_over_audio_prefix() -> None:
    assert (
        native.mapped_key("thinker.audio_tower.proj1.weight")
        == "model.multi_modal_projector.linear_1.weight"
    )
    assert (
        native.mapped_key("thinker.audio_tower.encoder.weight")
        == "model.audio_tower.encoder.weight"
    )


def test_version_drift_is_rejected(
    constructors: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import transformers

    monkeypatch.setattr(transformers, "__version__", "5.21.0")
    with pytest.raises(ForcedAlignerError, match="5.20.2"):
        native.load_native_model(constructors.path)
    assert constructors.state.calls == []


def test_missing_checkpoint_keys_are_refused(constructors: Any) -> None:
    from safetensors.torch import save_file

    save_file({}, str(constructors.path / "model.safetensors"))
    with pytest.raises(ForcedAlignerError, match="keys or tensor shapes"):
        native.load_native_model(constructors.path)
    assert constructors.state.calls == []


@pytest.fixture(autouse=True)
def clear_runtime() -> Any:
    runtime.release_forced_aligner_model_cache()
    yield
    runtime.release_forced_aligner_model_cache()


def test_prepared_assets_are_independently_verified_before_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        runtime.assets,
        "inspect_forced_aligner_model",
        lambda: SimpleNamespace(state="verified", path=Path("snapshot")),
    )
    monkeypatch.setattr(
        runtime.assets,
        "prepare_forced_aligner_model",
        lambda: pytest.fail("Prepared assets were fetched again"),
    )
    monkeypatch.setattr(
        runtime.assets,
        "verify_forced_aligner_model",
        lambda _path: calls.append("verify"),
    )
    monkeypatch.setattr(
        runtime,
        "load_native_processor",
        lambda _path: calls.append("processor") or object(),
    )
    path = runtime._prepared_path()
    first = runtime._processor(path)
    assert runtime._processor(path) is first
    assert calls == ["verify", "processor"]


def test_single_cpu_model_cached_until_release_without_changing_worker_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import torch

    threads = torch.get_num_threads()
    loads: list[Path] = []

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("CPU alignment must not inspect CUDA or change worker threads")

    monkeypatch.setattr(torch, "set_num_threads", forbidden)
    monkeypatch.setattr(torch.cuda, "is_available", forbidden)
    monkeypatch.setattr(torch.cuda, "mem_get_info", forbidden)
    monkeypatch.setattr(
        runtime.assets, "verify_forced_aligner_model", lambda _path: None
    )
    monkeypatch.setattr(
        runtime,
        "load_native_model",
        lambda path: loads.append(path) or object(),
    )
    first = runtime._runtime(Path("snapshot"))
    assert runtime._runtime(Path("snapshot")) is first
    assert loads == [Path("snapshot")]
    runtime.release_forced_aligner_model_cache()
    assert runtime._runtime(Path("snapshot")) is not first
    assert loads == [Path("snapshot"), Path("snapshot")]
    assert torch.get_num_threads() == threads


def test_oversized_audio_is_refused_before_preparing_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "_prepared_path",
        lambda: pytest.fail("Oversized input prepared assets"),
    )
    with pytest.raises(ForcedAlignerError, match="60 second"):
        runtime.align_audio(
            np.zeros(60 * 16000 + 1, dtype=np.float32), text="hello", language="en"
        )


def test_dense_prompt_is_refused_before_loading_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import torch

    processor = SimpleNamespace(
        prepare_forced_aligner_inputs=lambda **_kwargs: (
            {"input_ids": torch.zeros((1, 4097), dtype=torch.int64)},
            [["hello"]],
        )
    )
    monkeypatch.setattr(runtime, "_processor", lambda _path: processor)
    monkeypatch.setattr(
        runtime, "_runtime", lambda _path: pytest.fail("Oversized prompt loaded model")
    )
    with pytest.raises(ForcedAlignerError, match="4096 token"):
        runtime._align(
            Path("snapshot"), np.zeros(16000, dtype=np.float32), "hello", "en"
        )


@pytest.mark.usefixtures("download_progress_in_memory")
def test_unprepared_assets_queue_preparation_once_and_never_download(
    monkeypatch: pytest.MonkeyPatch, stub_celery_dispatch: list
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Alignment must not fetch, hash or load unprepared assets")

    monkeypatch.setattr(runtime.assets, "prepare_forced_aligner_model", forbidden)
    monkeypatch.setattr(runtime.assets, "verify_forced_aligner_model", forbidden)
    monkeypatch.setattr(runtime, "_align", forbidden)
    for _ in range(3):
        with pytest.raises(ForcedAlignerError, match="not prepared"):
            runtime.align_audio(
                np.zeros(160, dtype=np.float32), text="hello", language="en"
            )
    assert [kwargs for _name, _args, kwargs in stub_celery_dispatch] == [
        {"pinned_model": "forced_aligner", "include_core": False}
    ]
    assert runtime.assets.control.pending_task() is not None


def test_deletion_between_preparation_and_load_is_explicit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("snapshot")

    @contextmanager
    def file_lock(*_args: Any, **_kwargs: Any) -> Any:
        yield True

    resolutions = iter(
        [SimpleNamespace(state="verified", path=path), SimpleNamespace(state="missing")]
    )
    monkeypatch.setattr(
        runtime.assets, "inspect_forced_aligner_model", lambda: next(resolutions)
    )
    monkeypatch.setattr(type(runtime.assets.control), "model_lock", file_lock)
    monkeypatch.setattr(
        runtime, "_align", lambda *_args: pytest.fail("Must not load deleted assets")
    )
    with pytest.raises(ForcedAlignerError, match="assets changed"):
        runtime.align_audio(
            np.zeros(160, dtype=np.float32), text="hello", language="en"
        )


@pytest.mark.parametrize(
    "predictions",
    [[1, 1, 2, 3], [2, 3, 1, 4], [1, 4, 2, 3], [3, 2, 4, 5], [1, 2, 3, 15]],
)
def test_raw_predictions_never_repair_zero_reversed_or_outside_bounds(
    predictions: list[int],
) -> None:
    import torch

    logits = torch.zeros((1, 4, 16))
    logits[0, torch.arange(4), predictions] = 1
    ids = torch.full((1, 4), 7)
    config = SimpleNamespace(
        num_labels=16, timestamp_token_id=7, timestamp_segment_time=80
    )
    result = runtime.raw_alignment(logits, ids, ["hello", "world"], config, duration=1)
    assert len(result) == 2
    assert any(entry["start_time"] is None for entry in result)
    for entry, start, end in zip(result, predictions[::2], predictions[1::2]):
        if entry["start_time"] is not None:
            assert entry["start_time"] == start * 80 / 1000
            assert entry["end_time"] == end * 80 / 1000


def test_valid_raw_predictions_keep_observed_boundaries() -> None:
    import torch

    logits = torch.zeros((1, 4, 8))
    logits[0, torch.arange(4), [1, 2, 3, 4]] = 1
    config = SimpleNamespace(
        num_labels=8, timestamp_token_id=7, timestamp_segment_time=80
    )
    result = runtime.raw_alignment(
        logits, torch.full((1, 4), 7), ["hello", "world"], config, duration=1
    )
    assert [entry["text"] for entry in result] == ["hello", "world"]
    assert [entry["start_time"] for entry in result] == pytest.approx([0.08, 0.24])
    assert [entry["end_time"] for entry in result] == pytest.approx([0.16, 0.32])


def test_overlapping_positive_raw_words_keep_both_observed_intervals() -> None:
    import torch

    logits = torch.zeros((1, 4, 8))
    logits[0, torch.arange(4), [1, 3, 2, 4]] = 1
    config = SimpleNamespace(
        num_labels=8, timestamp_token_id=7, timestamp_segment_time=80
    )
    result = runtime.raw_alignment(
        logits, torch.full((1, 4), 7), ["hello", "world"], config, duration=1
    )
    assert [entry["start_time"] for entry in result] == pytest.approx([0.08, 0.16])
    assert [entry["end_time"] for entry in result] == pytest.approx([0.24, 0.32])
