"""ONNX ASR status reports a model only when the loader would find it complete.

The repo and file names are restated in backend/utils/onnx_asr_cache.py because
the API cannot import onnx-asr; the first tests pin them to the installed
onnx-asr and to the ids the engines load. The rest run on a real hub cache
layout (blobs, snapshot symlinks, refs) in a tmp directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from onnx_asr.loader import create_asr_resolver

from backend import preload_models
from backend.processing.engines.canary_engine import CanaryEngine
from backend.processing.engines.parakeet_engine import ParakeetEngine
from backend.tests.hf_cache_layout import (
    onnx_asr_files,
    write_hf_repo,
    write_onnx_asr_repo,
)
from backend.utils.onnx_asr_cache import ONNX_ASR_MODELS, ONNX_ASR_QUANTIZATIONS

ENGINES = {"parakeet": ParakeetEngine, "canary": CanaryEngine}


@pytest.fixture
def hub(model_cache_env, monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    return tmp_path / "hub"


@pytest.mark.parametrize("status_key", ONNX_ASR_MODELS)
def test_the_table_names_the_model_the_engine_loads(status_key):
    engine = ENGINES[status_key]()
    loaded_id = engine._to_onnx_asr_id(engine.default_model_id)

    assert ONNX_ASR_MODELS[status_key].onnx_asr_id == loaded_id


@pytest.mark.parametrize("status_key", ONNX_ASR_MODELS)
def test_the_repo_is_the_one_onnx_asr_downloads(status_key):
    model = ONNX_ASR_MODELS[status_key]

    assert model.repo_id == create_asr_resolver(model.onnx_asr_id).repo_id


@pytest.mark.parametrize("quantization", ONNX_ASR_QUANTIZATIONS)
@pytest.mark.parametrize("status_key", ONNX_ASR_MODELS)
def test_the_files_are_the_ones_onnx_asr_opens(status_key, quantization):
    model = ONNX_ASR_MODELS[status_key]
    model_type = create_asr_resolver(model.onnx_asr_id).model_type

    expected = set(model_type._get_model_files(quantization).values())

    assert set(model.files_for(quantization)) == expected


@pytest.mark.parametrize("quantization", ONNX_ASR_QUANTIZATIONS)
@pytest.mark.parametrize("status_key", ONNX_ASR_MODELS)
def test_a_complete_download_is_ready(status_key, quantization, hub):
    repo = write_onnx_asr_repo(hub, status_key, quantization)

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status[status_key]["downloaded"] is True
    assert status[status_key]["path"] == str(repo)


def test_hf_hub_cache_takes_precedence_over_hf_home(hub, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    write_onnx_asr_repo(tmp_path / "hf-home" / "hub", "parakeet")

    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert status["parakeet"]["downloaded"] is False

    repo = write_onnx_asr_repo(hub, "parakeet")

    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert status["parakeet"]["path"] == str(repo)


def test_a_download_still_in_progress_is_missing(hub):
    """huggingface_hub links a file into the snapshot only once it completes."""
    repo = write_hf_repo(hub, ONNX_ASR_MODELS["canary"].repo_id, {"vocab.txt": b"v"})
    (repo / "blobs" / "0f1e2d.incomplete").write_bytes(b"half an encoder")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["canary"]["downloaded"] is False


def test_a_download_missing_one_file_is_missing(hub):
    files = onnx_asr_files("parakeet", "int8")
    del files["decoder_joint-model.int8.onnx"]
    write_hf_repo(hub, ONNX_ASR_MODELS["parakeet"].repo_id, files)

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["parakeet"]["downloaded"] is False


def test_int8_and_fp32_halves_do_not_make_a_model(hub):
    """onnx-asr loads one precision; half of each loads in neither."""
    int8 = onnx_asr_files("canary", "int8")
    fp32 = onnx_asr_files("canary", None)
    mixed = {
        "encoder-model.int8.onnx": int8["encoder-model.int8.onnx"],
        "decoder-model.onnx": fp32["decoder-model.onnx"],
        "vocab.txt": int8["vocab.txt"],
    }
    write_hf_repo(hub, ONNX_ASR_MODELS["canary"].repo_id, mixed)

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["canary"]["downloaded"] is False


def test_a_snapshot_without_refs_main_is_missing(hub):
    """Offline, huggingface_hub finds the snapshot through refs/main only."""
    model = ONNX_ASR_MODELS["parakeet"]
    write_hf_repo(hub, model.repo_id, onnx_asr_files("parakeet", "int8"), ref=False)

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["parakeet"]["downloaded"] is False


@pytest.mark.parametrize(
    ("status_key", "nemo_repo"),
    [
        ("canary", "nvidia/canary-1b-v2"),
        ("parakeet", "nvidia/parakeet-tdt-0.6b-v3"),
    ],
)
def test_nvidias_nemo_checkpoint_is_neither_reported_nor_deleted(
    status_key, nemo_repo, hub
):
    """The NeMo repo shares the model's name but onnx-asr never loads it."""
    nemo = write_hf_repo(
        hub, nemo_repo, {**onnx_asr_files(status_key, "int8"), "model.nemo": b"x"}
    )

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status[status_key]["downloaded"] is False
    assert preload_models.delete_model(status_key) is False
    assert (nemo / "refs" / "main").exists()
    assert (nemo / "snapshots").is_dir()
