from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_STANDARDS_TARGETS: tuple[str, ...] = (
    "backend/api/error_handling.py",
    "backend/api/services/release_service.py",
    "backend/api/v1/endpoints/version.py",
    "backend/utils/deployment_warnings.py",
    "backend/utils/languages.py",
    "backend/utils/ollama_url_policy.py",
    "backend/utils/speaker_assignment.py",
    "scripts/check_fast.py",
)
COMMANDS: tuple[tuple[str, ...], ...] = (
    (sys.executable, "-m", "ruff", "check", *PYTHON_STANDARDS_TARGETS),
    (sys.executable, "-m", "ruff", "format", "--check", *PYTHON_STANDARDS_TARGETS),
    (sys.executable, "-m", "mypy", *PYTHON_STANDARDS_TARGETS[:-1]),
)


def main() -> int:
    for command in COMMANDS:
        print(f"+ {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=REPO_ROOT)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
