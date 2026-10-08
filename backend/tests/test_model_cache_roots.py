"""Model status and deletion only look where the loaders actually load from.

Status used to fall back to ~/.cache/huggingface/hub and ~/.cache/whisper even
when HF_HOME or XDG_CACHE_HOME pointed elsewhere. The loaders never read those
fallbacks, so a model found there was reported as downloaded while the engine
would download it again, and deletion, which resolves its target through the
same status check, removed models from the user's personal cache on a
bare-metal install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import preload_models
from backend.tests.hf_cache_layout import (
    write_onnx_asr_repo,
    write_pyannote_embedding,
)

PARAKEET_REPO = "models--istupakov--parakeet-tdt-0.6b-v3-onnx"


@pytest.fixture
def homes(model_cache_env, tmp_path) -> dict[str, Path]:
    """An empty personal HOME and an empty managed cache, nothing else set."""
    managed = tmp_path / "managed"
    managed.mkdir()
    return {"home": model_cache_env, "managed": managed}


@pytest.fixture
def no_bundled_pyannote(monkeypatch, tmp_path) -> None:
    """The repo ships Pyannote models; point the bundled root at nothing."""
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(tmp_path / "no-bundled"))


def _personal_hub(home: Path) -> Path:
    return home / ".cache" / "huggingface" / "hub"


def test_an_onnx_model_outside_hf_home_is_not_reported(homes, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    write_onnx_asr_repo(_personal_hub(homes["home"]), "parakeet")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["parakeet"]["downloaded"] is False
    assert status["parakeet"]["checked_paths"] == [
        str(homes["managed"] / "hub" / PARAKEET_REPO)
    ]


def test_the_hub_cache_follows_xdg_cache_home_like_huggingface_hub(homes, monkeypatch):
    """No HF_HOME: huggingface_hub caches under $XDG_CACHE_HOME/huggingface."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(homes["managed"]))
    model = write_onnx_asr_repo(homes["managed"] / "huggingface" / "hub", "parakeet")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["parakeet"]["downloaded"] is True
    assert status["parakeet"]["path"] == str(model)


def test_a_whisper_model_outside_xdg_cache_home_is_not_reported(homes, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(homes["managed"]))
    personal = homes["home"] / ".cache" / "whisper"
    personal.mkdir(parents=True)
    (personal / "large-v3-turbo.pt").write_bytes(b"weights")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["whisper"]["downloaded"] is False


def test_deleting_never_reaches_the_personal_hf_cache(homes, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    personal = write_onnx_asr_repo(_personal_hub(homes["home"]), "parakeet")

    deleted = preload_models.delete_model("parakeet")

    assert deleted is False
    assert (personal / "refs" / "main").exists()


@pytest.mark.usefixtures("no_bundled_pyannote")
def test_a_pyannote_model_found_outside_the_managed_cache_is_not_deleted(
    homes, monkeypatch
):
    """Status may load Pyannote from the personal cache; delete must refuse it."""
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    repo = write_pyannote_embedding(_personal_hub(homes["home"]))

    with pytest.raises(ValueError, match="outside Nojoin's model cache"):
        preload_models.delete_model("embedding")

    assert (repo / "refs" / "main").exists()
    assert list((repo / "blobs").iterdir())


def test_a_model_in_the_managed_cache_is_still_deleted(homes, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    model = write_onnx_asr_repo(homes["managed"] / "hub", "parakeet")

    assert preload_models.delete_model("parakeet") is True
    assert not model.exists()


@pytest.mark.usefixtures("no_bundled_pyannote")
@pytest.mark.parametrize("variable", ["XDG_CACHE_HOME", "HF_HUB_CACHE"])
def test_pyannote_is_found_in_the_cache_its_loader_downloads_into(
    variable, homes, monkeypatch
):
    """from_pretrained(model_id) downloads into huggingface_hub's own cache.

    Pyannote status used to check only $HF_HOME/hub and ~/.cache, so with the
    cache moved by XDG_CACHE_HOME or HF_HUB_CACHE a model its loader had just
    downloaded showed as Missing and could never be deleted.
    """
    monkeypatch.setenv(variable, str(homes["managed"]))
    hub = (
        homes["managed"] / "huggingface" / "hub"
        if variable == "XDG_CACHE_HOME"
        else homes["managed"]
    )
    repo = write_pyannote_embedding(hub)

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["embedding"]["downloaded"] is True
    assert status["embedding"]["source"] == "cache"
    assert status["embedding"]["path"].startswith(str(repo))


@pytest.mark.usefixtures("no_bundled_pyannote")
def test_a_pyannote_model_in_the_personal_cache_is_reported_as_external(
    homes, monkeypatch
):
    """It is still loaded from there, so it is Ready, but it is not Nojoin's."""
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    write_pyannote_embedding(_personal_hub(homes["home"]))

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["embedding"]["downloaded"] is True
    assert status["embedding"]["source"] == "external"


@pytest.mark.usefixtures("no_bundled_pyannote")
def test_deleting_pyannote_removes_the_whole_repo(homes, monkeypatch):
    """Status points at a snapshot of symlinks; the weights are in blobs/."""
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    repo = write_pyannote_embedding(homes["managed"] / "hub")

    assert preload_models.delete_model("embedding") is True

    assert not repo.exists()
    assert (homes["managed"] / "hub").is_dir()


@pytest.mark.usefixtures("no_bundled_pyannote")
def test_a_hub_root_over_home_cannot_reach_the_personal_cache(homes, monkeypatch):
    """The model's own repo under the root is deletable, nothing else under it.

    With HF_HUB_CACHE pointed at HOME, the personal cache sits inside the
    managed root, which a check against the root alone accepted.
    """
    monkeypatch.setenv("HF_HUB_CACHE", str(homes["home"]))
    personal = write_pyannote_embedding(_personal_hub(homes["home"]))

    with pytest.raises(ValueError, match="outside Nojoin's model cache"):
        preload_models.delete_model("embedding")

    assert (personal / "refs" / "main").exists()


def test_a_repo_linked_to_outside_the_cache_is_not_deleted(
    homes, monkeypatch, tmp_path
):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    elsewhere = write_onnx_asr_repo(tmp_path / "elsewhere", "parakeet")
    link = homes["managed"] / "hub" / PARAKEET_REPO
    link.parent.mkdir()
    link.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ValueError, match="is a link to"):
        preload_models.delete_model("parakeet")

    assert link.is_symlink()
    assert (elsewhere / "refs" / "main").exists()


def test_a_repo_linked_within_the_cache_is_refused_cleanly(homes, monkeypatch):
    """Not an OSError from rmtree on a symlink, which surfaced as a 500."""
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    hub = homes["managed"] / "hub"
    real = write_onnx_asr_repo(hub / "moved", "parakeet")
    link = hub / PARAKEET_REPO
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="Remove it by hand"):
        preload_models.delete_model("parakeet")

    assert link.is_symlink()
    assert (real / "refs" / "main").exists()


def test_delete_resolves_dotdot_after_a_link_as_the_loader_does(
    homes, monkeypatch, tmp_path
):
    """``link/../hub`` is a sibling of the link's target, not of the link.

    The OS follows the link before applying "..", and so do the loaders and
    status. Collapsing the root as text deleted a different directory, one
    status never reported.
    """
    (tmp_path / "deep" / "x").mkdir(parents=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "link").symlink_to(
        tmp_path / "deep" / "x", target_is_directory=True
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "a" / "link" / ".." / "hub"))
    loaded = write_onnx_asr_repo(tmp_path / "deep" / "hub", "parakeet")
    lookalike = write_onnx_asr_repo(tmp_path / "a" / "hub", "parakeet")

    assert preload_models.delete_model("parakeet") is True

    assert not loaded.exists()
    assert (lookalike / "refs" / "main").exists()


def test_a_whisper_model_is_deleted_from_its_cache(homes, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(homes["managed"]))
    model = homes["managed"] / "whisper" / "large-v3-turbo.pt"
    model.parent.mkdir()
    model.write_bytes(b"weights")

    assert preload_models.delete_model("whisper", whisper_model_size="turbo") is True
    assert not model.exists()


def test_a_whisper_file_linked_to_outside_the_cache_is_not_deleted(
    homes, monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(homes["managed"]))
    elsewhere = tmp_path / "large-v3-turbo.pt"
    elsewhere.write_bytes(b"weights")
    link = homes["managed"] / "whisper" / "large-v3-turbo.pt"
    link.parent.mkdir()
    link.symlink_to(elsewhere)

    with pytest.raises(ValueError, match="is a link to"):
        preload_models.delete_model("whisper", whisper_model_size="turbo")

    assert elsewhere.exists()
