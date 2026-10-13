"""Third-party libraries must not send their own telemetry (docs/TELEMETRY.md)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ort_flag_after_importing_backend(preset: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    if preset is not None:
        env["ORT_DISABLE_TELEMETRY"] = preset
    # A fresh interpreter, because this test process imported backend long ago.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, backend; print(os.environ['ORT_DISABLE_TELEMETRY'])",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1]


def test_importing_backend_disables_onnx_runtime_telemetry():
    assert _ort_flag_after_importing_backend(None) == "1"


def test_an_operators_explicit_value_is_left_alone():
    assert _ort_flag_after_importing_backend("0") == "0"


def test_worker_image_disables_onnx_runtime_telemetry():
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile.worker").read_text()
    runtime_stage = dockerfile.split("AS runtime", 1)[1]
    assert "ENV ORT_DISABLE_TELEMETRY=1" in runtime_stage
