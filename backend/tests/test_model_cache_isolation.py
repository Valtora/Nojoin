"""The default test environment keeps model writes out of the host's caches."""

from pathlib import Path

import pytest
from huggingface_hub import hf_hub_download, snapshot_download

from backend.processing.text_embedding import fastembed_cache_dir
from backend.tests.hf_cache_layout import COMMIT, write_hf_repo
from backend.utils import nemotron_lock
from backend.utils.model_cache_paths import hf_hub_cache_root, whisper_cache_root
from backend.utils.pyannote_model_utils import resolve_local_pyannote_model


def test_default_model_caches_and_nemotron_writes_are_temporary(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Take no cache fixture: ordinary backend tests must already be isolated."""
    base = tmp_path_factory.getbasetemp()
    hub = Path(hf_hub_cache_root())
    for root in (hub, Path(whisper_cache_root()), Path(fastembed_cache_dir())):
        assert root.is_relative_to(base)
        assert not root.exists()

    # Pyannote also searches HOME's personal hub, even with a managed cache.
    resolution = resolve_local_pyannote_model("nojoin/test-cache-isolation")
    for checked in resolution.checked_paths:
        assert Path(checked).is_relative_to(base)

    control = hub / ".locks" / "nojoin-nemotron"
    assert not control.exists()
    assert nemotron_lock.pending_task() is None
    with nemotron_lock.model_lock() as acquired:
        assert acquired
        nemotron_lock.write_pending("isolated-task")
        assert (control / "prepare.lock").is_file()
        assert (control / "pending.json").is_file()
        marker = nemotron_lock.pending_task()
        assert marker is not None and marker["task_id"] == "isolated-task"
        nemotron_lock.clear_pending("isolated-task")
    assert not (control / "pending.json").exists()


def test_implicit_hub_loaders_use_the_same_temporary_cache(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """ONNX and Pyannote omit cache_dir; HF's imported defaults must agree."""
    hub = Path(hf_hub_cache_root())
    assert hub.is_relative_to(tmp_path_factory.getbasetemp())
    repo = write_hf_repo(hub, "nojoin/test-cache-isolation", {"config.json": b"{}"})
    snapshot = repo / "snapshots" / COMMIT

    assert (
        Path(snapshot_download("nojoin/test-cache-isolation", local_files_only=True))
        == snapshot
    )
    assert (
        Path(
            hf_hub_download(
                "nojoin/test-cache-isolation", "config.json", local_files_only=True
            )
        )
        == snapshot / "config.json"
    )
