"""Hub-managed transfers of the three immutable native speaker assets."""

from __future__ import annotations

import os
from pathlib import Path

from backend.utils.model_cache_paths import hf_hub_cache_root
from backend.utils.nemotron_manifest import (
    REPOSITORY,
    REVISION,
    NemotronModelError,
    NemotronOfflineError,
)


def download_asset(destination: Path) -> None:
    """Fetch one pinned file; leave timeouts and transfer retries to the Hub."""
    # Keep status inspection free of Hub imports. Check the environment on each
    # preparation too, since Hub constants are captured at its first import.
    from httpx import HTTPError
    from huggingface_hub import constants, hf_hub_download

    flag = os.getenv("HF_HUB_OFFLINE")
    offline = (
        flag.upper() in {"1", "ON", "YES", "TRUE"}
        if flag is not None
        else constants.HF_HUB_OFFLINE
    )
    if offline:
        raise NemotronOfflineError("Native speaker model is not prepared (offline).")
    try:
        repair = destination.exists() or destination.is_symlink()
        # Hub can return an existing corrupt pointer, even after a forced blob
        # download. Remove only this entry, never write through its symlink.
        destination.unlink(missing_ok=True)
        hf_hub_download(
            repo_id=REPOSITORY,
            filename=destination.name,
            revision=REVISION,
            cache_dir=hf_hub_cache_root(),
            force_download=repair,
            local_files_only=False,
        )
    except (OSError, ValueError, HTTPError) as exc:
        raise NemotronModelError("Native speaker asset transfer failed") from exc
