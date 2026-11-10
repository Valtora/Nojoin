"""The cloud model defaults load the same way whatever is imported first.

Each case runs in a fresh interpreter, because the import order is the thing
under test and this process has already imported everything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLOUD_MODEL_KEYS = ("gemini_model", "openai_model", "anthropic_model")
# No provider has a default model (docs/ADMIN.md): the administrator picks one.
EXPECTED_MODELS = dict.fromkeys(CLOUD_MODEL_KEYS)

_PROBE = """
import importlib
import json
import sys

for name in sys.argv[1:]:
    importlib.import_module(name)

from backend.utils.config_defaults import DEFAULT_USER_SETTINGS

keys = {keys!r}
print(json.dumps({{
    "models": {{key: DEFAULT_USER_SETTINGS[key] for key in keys}},
    "llm_modules": sorted(
        name for name in sys.modules
        if name.startswith(("backend.processing.llm_backends", "backend.processing.llm_services"))
    ),
}}))
""".format(keys=CLOUD_MODEL_KEYS)


def _import_in_fresh_interpreter(tmp_path: Path, *modules: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "NOJOIN_DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": str(REPO_ROOT),
    }
    process = subprocess.run(
        [sys.executable, "-c", _PROBE, *modules],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stderr[-2000:]
    result: dict[str, Any] = json.loads(process.stdout.strip().splitlines()[-1])
    return result


@pytest.mark.parametrize(
    "first", ["backend.utils.config_defaults", "backend.utils.config_manager"]
)
def test_the_config_loads_first_without_reaching_the_llm_backends(
    tmp_path: Path, first: str
) -> None:
    result = _import_in_fresh_interpreter(tmp_path, first)

    assert result["llm_modules"] == []
    assert result["models"] == EXPECTED_MODELS


@pytest.mark.parametrize(
    "order",
    [
        ("backend.processing.llm_services", "backend.utils.config_defaults"),
        ("backend.processing.llm_backends.factory", "backend.utils.config_manager"),
        ("backend.processing.llm_backends.anthropic",),
    ],
)
def test_every_import_order_gives_the_same_cloud_model_defaults(
    tmp_path: Path, order: tuple[str, ...]
) -> None:
    result = _import_in_fresh_interpreter(tmp_path, *order)

    assert result["models"] == EXPECTED_MODELS
