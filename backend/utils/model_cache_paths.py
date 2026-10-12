"""Where Nojoin's model loaders keep their caches.

Model status, model deletion and the loaders themselves all resolve these
roots here, so a model is reported where it is loaded from and deleted only
there. Every function reads the environment on each call: the API process
reports status for caches it never downloads into, and huggingface_hub fixes
its own constants once, at import time.
"""

from __future__ import annotations

import os


def _default_cache_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache")


def whisper_cache_root() -> str:
    """The directory the Whisper loaders download into and load from.

    The same root whisper.load_model uses when given no download_root
    (``${XDG_CACHE_HOME:-~/.cache}/whisper``), passed to it explicitly.
    """
    return os.path.join(os.getenv("XDG_CACHE_HOME", _default_cache_home()), "whisper")


def hf_hub_cache_root() -> str:
    """The Hugging Face hub cache that huggingface_hub downloads into.

    Mirrors huggingface_hub 1.33.0 ``constants.py`` (lines 159-183, the same
    in 1.32.0) step for step: ``HF_HUB_CACHE``, then
    ``HUGGINGFACE_HUB_CACHE``, then ``$HF_HOME/hub``, then
    ``${XDG_CACHE_HOME:-~/.cache}/huggingface/hub``.
    That includes its quirks: a variable set to the empty string counts as set
    (an empty ``HF_HOME`` gives the relative path ``hub``), and each value is
    passed through ``expanduser`` before ``expandvars``.
    """
    hf_home = os.path.expandvars(
        os.path.expanduser(
            os.getenv(
                "HF_HOME",
                os.path.join(
                    os.getenv("XDG_CACHE_HOME", _default_cache_home()), "huggingface"
                ),
            )
        )
    )
    legacy_hub_cache = os.getenv("HUGGINGFACE_HUB_CACHE", os.path.join(hf_home, "hub"))
    return os.path.expandvars(
        os.path.expanduser(os.getenv("HF_HUB_CACHE", legacy_hub_cache))
    )


def hf_repo_dirname(repo_id: str) -> str:
    """The directory huggingface_hub caches a model repo under, in the hub cache."""
    return f"models--{repo_id.replace('/', '--')}"
