"""Finding the onnx-asr models in the Hugging Face hub cache without onnx-asr.

Model status runs in the API, whose image ships without onnx-asr, so the repo
each model downloads from and the files the loader opens are restated here.
backend/tests/test_onnx_asr_cache.py checks both against the pinned onnx-asr
and against the ids the engines pass to it.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from .model_cache_paths import hf_hub_cache_root, hf_repo_dirname

# The two weight precisions onnx-asr offers for these models: fp32 (None) and int8.
ONNX_ASR_QUANTIZATIONS: tuple[str | None, ...] = (None, "int8")


@dataclass(frozen=True)
class OnnxAsrModel:
    """One onnx-asr model as the engine loads it.

    ``model_files`` are the glob patterns onnx-asr resolves inside the snapshot
    (its ``_get_model_files``), with ``{q}`` standing for the quantization
    suffix: empty for fp32, ``?int8`` for int8.

    ``fp32_external_data`` are the files the fp32 graphs keep their weights in
    (ONNX external data, ``<graph>.onnx.data``). ``_get_model_files`` does not
    list them, but onnx-asr's download fetches them (it adds
    ``<graph>.onnx?data`` to its allow patterns) and onnxruntime cannot load a
    graph without its external data. The int8 graphs embed their weights.

    ``cpu_quantization`` is the precision the engine loads on a CPU. A GPU
    always loads fp32 (onnx_asr_engine.onnx_asr_quantization).
    """

    onnx_asr_id: str
    repo_id: str
    model_files: tuple[str, ...]
    fp32_external_data: tuple[str, ...]
    cpu_quantization: str | None

    @property
    def quantizations(self) -> tuple[str | None, ...]:
        """Every precision the engine loads this model at, on some device."""
        return tuple(dict.fromkeys((None, self.cpu_quantization)))

    @property
    def repo_dirname(self) -> str:
        return hf_repo_dirname(self.repo_id)

    def files_for(self, quantization: str | None) -> tuple[str, ...]:
        suffix = f"?{quantization}" if quantization else ""
        return tuple(pattern.format(q=suffix) for pattern in self.model_files)

    def required_files(self, quantization: str | None) -> tuple[str, ...]:
        """Every file a load at this precision opens: the graphs and their weights."""
        external_data = self.fp32_external_data if quantization is None else ()
        return self.files_for(quantization) + external_data


# Keyed by the model status key. The repo ids are onnx-asr 0.12.0's
# resolver.model_repos entries for the ids the engines load. In both repos only
# the fp32 encoder has external data, and it is most of the download:
# encoder-model.onnx.data is 2.4 GB for Parakeet and 3.3 GB for Canary.
# Parakeet loads fp32 on CPU too: its int8 encoder blanks out whole stretches of
# clear speech, and where depends on where the window starts (#445). Canary's
# int8 weights held up at the same window length.
ONNX_ASR_MODELS = {
    "parakeet": OnnxAsrModel(
        onnx_asr_id="nemo-parakeet-tdt-0.6b-v3",
        repo_id="istupakov/parakeet-tdt-0.6b-v3-onnx",
        model_files=(
            "encoder-model{q}.onnx",
            "decoder_joint-model{q}.onnx",
            "vocab.txt",
        ),
        fp32_external_data=("encoder-model.onnx.data",),
        cpu_quantization=None,
    ),
    "canary": OnnxAsrModel(
        onnx_asr_id="nemo-canary-1b-v2",
        repo_id="istupakov/canary-1b-v2-onnx",
        model_files=("encoder-model{q}.onnx", "decoder-model{q}.onnx", "vocab.txt"),
        fp32_external_data=("encoder-model.onnx.data",),
        cpu_quantization="int8",
    ),
}


def onnx_asr_model(onnx_asr_id: str) -> OnnxAsrModel | None:
    """The table's entry for an onnx-asr model id, if it has one."""
    return next(
        (m for m in ONNX_ASR_MODELS.values() if m.onnx_asr_id == onnx_asr_id), None
    )


def _cached_snapshot(repo_dir: str) -> str | None:
    """The snapshot an offline huggingface_hub lookup of ``main`` returns.

    The commit comes from ``refs/main``, read verbatim as huggingface_hub
    reads it. Without that ref there is no cached snapshot to load, whatever
    sits under ``snapshots/``.
    """
    try:
        with open(os.path.join(repo_dir, "refs", "main"), encoding="utf-8") as ref:
            commit = ref.read()
    except OSError:
        return None
    snapshot = os.path.join(repo_dir, "snapshots", commit)
    return snapshot if commit and os.path.isdir(snapshot) else None


def _has_every_file(snapshot: str, patterns: tuple[str, ...]) -> bool:
    """Whether onnx-asr would resolve every pattern to exactly one file.

    ``isfile`` follows the snapshot's symlinks into ``blobs/``, so a blob still
    downloading (``*.incomplete``, never linked) or a dangling link counts as
    missing. Like onnx-asr, a missing ``.onnx`` may be stood in for by one
    ``.ort`` file of the same name.
    """
    root = glob.escape(snapshot)
    for pattern in patterns:
        matches = glob.glob(os.path.join(root, pattern))
        if not matches and pattern.endswith(".onnx"):
            matches = glob.glob(
                os.path.join(root, pattern.removesuffix(".onnx") + ".ort")
            )
        if len(matches) != 1 or not os.path.isfile(matches[0]):
            return False
    return True


def _external_data_beside_graphs(
    snapshot: str, model: OnnxAsrModel, quantization: str | None
) -> bool:
    """Whether ONNX Runtime accepts where each graph's external data lives.

    It resolves a graph to its real file and refuses external data whose real
    path falls outside that file's directory. A plain hub cache keeps every
    blob of a repo in its own ``blobs/``, so they always pass. huggingface_hub
    1.32+ moves Xet downloads into a store sharded by hash, which puts a graph
    and its weights in different directories (#448).
    """
    if quantization is not None:
        return True
    for data_name in model.fp32_external_data:
        graph = os.path.realpath(
            os.path.join(snapshot, data_name.removesuffix(".data"))
        )
        graph_dir = os.path.dirname(graph)
        data = os.path.realpath(os.path.join(snapshot, data_name))
        if os.path.commonpath([graph_dir, data]) != graph_dir:
            return False
    return True


def find_unloadable_onnx_asr_copy(
    model: OnnxAsrModel, quantization: str | None
) -> str | None:
    """The repo directory, when its complete copy at this precision cannot load.

    That is a copy whose weights a shared blob store keeps away from their
    graph. Every file is present, so onnx-asr would not download anything
    again, but ONNX Runtime refuses the graph every time.
    """
    repo_dir = os.path.join(hf_hub_cache_root(), model.repo_dirname)
    snapshot = _cached_snapshot(repo_dir)
    if snapshot is None or not _has_every_file(
        snapshot, model.required_files(quantization)
    ):
        return None
    if _external_data_beside_graphs(snapshot, model, quantization):
        return None
    return repo_dir


def _has_partial_download(repo_dir: str) -> bool:
    """Whether a blob of the repo was left part-way through downloading.

    huggingface_hub writes each file as ``blobs/<etag>.incomplete`` and links
    it into the snapshot only once it completes, so the snapshot alone cannot
    tell a finished download from one whose remaining files are still in
    flight, or were cut off and will resume.
    """
    pattern = os.path.join(glob.escape(repo_dir), "blobs", "*.incomplete")
    return bool(glob.glob(pattern))


def find_cached_onnx_asr_model(model: OnnxAsrModel) -> str | None:
    """The model's repo directory in the hub cache, if a complete copy is there.

    Complete means the snapshot ``refs/main`` points at holds every file the
    loader opens, for a precision the engine loads it at (a mix of int8 and
    fp32 files loads in neither), laid out so ONNX Runtime will load them, and
    no file of the repo is part-way through downloading. Only
    the exact repo directory is considered, so another repo with a similar
    name (NVIDIA's own NeMo checkpoint, say) never counts.
    """
    repo_dir = os.path.join(hf_hub_cache_root(), model.repo_dirname)
    snapshot = _cached_snapshot(repo_dir)
    if snapshot is None or _has_partial_download(repo_dir):
        return None
    if any(
        _has_every_file(snapshot, model.required_files(quantization))
        and _external_data_beside_graphs(snapshot, model, quantization)
        for quantization in model.quantizations
    ):
        return repo_dir
    return None
