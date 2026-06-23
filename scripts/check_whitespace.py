#!/usr/bin/env python3
"""Fail if any tracked text file carries trailing whitespace.

This is the CI-side guard that keeps the trailing-whitespace cleanup (SRC-013)
from regressing. Python is already covered by ``ruff format --check`` and the
frontend by the ``no-trailing-spaces`` ESLint rule, but Markdown, YAML, shell,
and config files have no other gate in CI, so this script covers everything.

Vendored upstream model cards under ``bundled_models/`` are excluded: they are
third-party artefacts we do not reformat. Binary files are skipped.

    python scripts/check_whitespace.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Path prefixes that are tracked but not ours to reformat.
EXCLUDED_PREFIXES = ("bundled_models/",)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_excluded(path: str) -> bool:
    return path.startswith(EXCLUDED_PREFIXES)


def trailing_whitespace_lines(path: Path) -> list[int]:
    raw = path.read_bytes()
    if b"\x00" in raw:  # binary file
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    offenders = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line != line.rstrip(" \t"):
            offenders.append(lineno)
    return offenders


def main() -> int:
    failures: list[str] = []
    for rel_path in tracked_files():
        if is_excluded(rel_path):
            continue
        abs_path = REPO_ROOT / rel_path
        if not abs_path.is_file():
            continue
        for lineno in trailing_whitespace_lines(abs_path):
            failures.append(f"{rel_path}:{lineno}")

    if failures:
        print("Trailing whitespace found:")
        for entry in failures:
            print(f"  {entry}")
        print(f"\n{len(failures)} line(s) with trailing whitespace.")
        return 1

    print("No trailing whitespace in tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
