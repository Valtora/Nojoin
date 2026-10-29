"""Inspect and prepare pinned forced-aligner files without inference imports."""

from collections.abc import Callable
from pathlib import Path

from backend.utils.forced_aligner_manifest import (
    ForcedAlignerError,
    load_asset_manifest,
    manifest_sha256,
    validate_metadata,
)
from backend.utils.pinned_model_assets import PinnedModelAssets, PinnedModelResolution
from backend.utils.pinned_model_control import PinnedModelControl

control = PinnedModelControl("forced-aligner", lambda: manifest_sha256())
_assets = PinnedModelAssets(
    control,
    lambda: load_asset_manifest(),
    validate_metadata,
    ForcedAlignerError,
    "Forced aligner",
    discardable_files=("README.md", ".gitattributes"),
)


def forced_aligner_repo_path() -> Path:
    """Resolve only Nojoin's managed repository."""
    return _assets.repo_path()


def inspect_forced_aligner_model() -> PinnedModelResolution:
    """Read receipt metadata without loading, hashing or downloading weights."""
    return _assets.inspect()


def verify_forced_aligner_model(path: Path) -> None:
    """Verify actual pinned bytes independently before loading."""
    _assets.verify(path)


def prepare_forced_aligner_model(
    *, progress: Callable[[str, int], None] | None = None
) -> Path:
    """Synchronously prepare immutable assets without touching resident models."""
    if progress is not None:
        return _assets.prepare(progress=progress)
    from backend.utils.download_progress import set_download_progress

    def report(message: str, percent: int) -> None:
        set_download_progress(
            percent,
            message,
            stage="forced_aligner",
            status="complete" if percent == 100 else "downloading",
        )

    try:
        return _assets.prepare(progress=report)
    except (OSError, ValueError, RuntimeError):
        set_download_progress(
            0,
            "Forced aligner model preparation failed.",
            stage="forced_aligner",
            status="error",
        )
        raise
