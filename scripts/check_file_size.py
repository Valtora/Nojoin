#!/usr/bin/env python3
"""Fail if a backend Python source file grows past the review size threshold.

Large modules are a maintainability hotspot, so new source files must stay at or
below ``MAX_LINES`` lines (BE-008). Tests and generated Alembic migrations are
exempt -- fixtures and committed-immutable revisions legitimately run long.

Files that already exceeded the limit when the gate was introduced are listed in
``GRANDFATHERED`` with their line count at that time. A grandfathered file passes
while it stays at or below its recorded count, but FAILS if it grows: the policy
is shrink-not-grow. The goal is to decompose these over time and delete each
entry as it drops back under ``MAX_LINES``.

    python scripts/check_file_size.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# New source files must be at or below this many lines.
MAX_LINES = 1000

# Only backend source is gated.
INCLUDED_PREFIX = "backend/"

# Path prefixes / patterns that are exempt from the size gate.
EXEMPT_PREFIXES = ("backend/tests/", "backend/alembic/versions/")


# BE-008 file-size baseline: source files that already exceeded MAX_LINES when
# the gate was introduced, mapped to their line count at that time. A listed
# file is allowed up to its recorded count and FAILS if it grows beyond it. Do
# not add new entries; shrink and remove them over time.
GRANDFATHERED: dict[str, int] = {
    "backend/worker/tasks/pipeline.py": 2867,
    "backend/processing/llm_services.py": 2686,
    "backend/services/calendar_service.py": 2571,
    "backend/utils/canonical_pipeline/diarization.py": 2485,
    "backend/utils/canonical_pipeline/core.py": 2192,
    "backend/core/backup_manager.py": 2147,
    "backend/processing/live_transcribe.py": 1973,
    "backend/utils/canonical_pipeline/speaker.py": 1342,
}


def tracked_python_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "backend/**/*.py", "backend/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_exempt(path: str) -> bool:
    if not path.startswith(INCLUDED_PREFIX):
        return True
    if path.startswith(EXEMPT_PREFIXES):
        return True
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def count_lines(path: str) -> int:
    with (REPO_ROOT / path).open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def main() -> int:
    violations: list[str] = []

    for path in tracked_python_files():
        if is_exempt(path):
            continue
        lines = count_lines(path)
        baseline = GRANDFATHERED.get(path)
        if baseline is not None:
            if lines > baseline:
                violations.append(
                    f"{path}: {lines} lines exceeds its grandfathered baseline of "
                    f"{baseline} (shrink, do not grow)."
                )
        elif lines > MAX_LINES:
            violations.append(
                f"{path}: {lines} lines exceeds the {MAX_LINES}-line limit for new "
                f"source files."
            )

    if violations:
        print("File-size check failed:")
        for message in violations:
            print(f"  {message}")
        print(
            "\nKeep new backend source at or below "
            f"{MAX_LINES} lines; grandfathered files must shrink, not grow."
        )
        return 1

    print("File-size check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
