"""Importing the VAD module leaves torch's CPU thread count alone (#435)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
VAD_MODULE = BACKEND / "processing" / "vad.py"


def test_importing_vad_keeps_torch_thread_count():
    # A fresh interpreter, because silero_vad changes the count only on its
    # first import and this process may have imported it already.
    code = (
        "import torch\n"
        "torch.set_num_threads(3)\n"
        "import backend.processing.vad\n"
        "print(torch.get_num_threads())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-1] == "3"


def _imports_silero_vad(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "silero_vad" for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return node.module.split(".")[0] == "silero_vad"
    return False


def test_only_the_vad_module_imports_silero_vad():
    # Any other import could run first in a worker and pin the count again.
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] == "tests" or path == VAD_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"backend/{relative.as_posix()}:{node.lineno}"
            for node in ast.walk(tree)
            if _imports_silero_vad(node)
        )

    assert offenders == [], (
        "Import silero_vad through backend.processing.vad, which keeps torch's "
        f"thread count: {offenders}"
    )
