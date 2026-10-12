"""The hub cache root Nojoin resolves is the one huggingface_hub downloads into.

Status and deletion cannot import huggingface_hub to ask (the library fixes its
constants at import time, and status runs in the API), so they re-implement its
resolution. Each case here runs the installed huggingface_hub in a fresh
interpreter with the same environment and compares the two answers, so a
mismatch in precedence, an empty variable, or the order of expanduser and
expandvars fails here rather than in someone's cache.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.utils.model_cache_paths import hf_hub_cache_root

ASK_HUGGINGFACE_HUB = (
    "from huggingface_hub import constants; print(constants.HF_HUB_CACHE)"
)

CASES = {
    "nothing set": {},
    "XDG_CACHE_HOME": {"XDG_CACHE_HOME": "/xdg"},
    "HF_HOME over XDG_CACHE_HOME": {"HF_HOME": "/hf-home", "XDG_CACHE_HOME": "/xdg"},
    "HUGGINGFACE_HUB_CACHE over HF_HOME": {
        "HUGGINGFACE_HUB_CACHE": "/legacy-hub",
        "HF_HOME": "/hf-home",
    },
    "HF_HUB_CACHE over everything": {
        "HF_HUB_CACHE": "/hub",
        "HUGGINGFACE_HUB_CACHE": "/legacy-hub",
        "HF_HOME": "/hf-home",
        "XDG_CACHE_HOME": "/xdg",
    },
    "an empty HF_HOME is set, not unset": {"HF_HOME": "", "XDG_CACHE_HOME": "/xdg"},
    "an empty XDG_CACHE_HOME is set, not unset": {"XDG_CACHE_HOME": ""},
    "tilde inside a variable stays literal": {
        "HF_HUB_CACHE": "$NOJOIN_TEST_ROOT/hub",
        "NOJOIN_TEST_ROOT": "~/elsewhere",
    },
    "tilde and variables in HF_HOME": {
        "HF_HOME": "~/$NOJOIN_TEST_ROOT",
        "NOJOIN_TEST_ROOT": "hf",
    },
}


@pytest.mark.parametrize("variables", CASES.values(), ids=CASES.keys())
def test_the_hub_cache_root_matches_huggingface_hub(
    variables, model_cache_env, monkeypatch, tmp_path
):
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    environment = {
        "HOME": str(model_cache_env),
        "PATH": os.environ.get("PATH", ""),
        **variables,
    }

    library = subprocess.run(
        [sys.executable, "-c", ASK_HUGGINGFACE_HUB],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert hf_hub_cache_root() == library
