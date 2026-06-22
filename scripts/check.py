#!/usr/bin/env python3
"""Run the same fast backend checks that CI enforces, in one command.

This mirrors the Python-side jobs in .github/workflows/ci.yml so contributors
can reproduce CI locally before pushing:

    python scripts/check.py            # run every check, report at the end
    python scripts/check.py --fix      # auto-fix lint and formatting first
    python scripts/check.py lint format # run only the named checks

Frontend checks (lint, test, build) are not run here; run them from frontend/
with npm as documented in CONTRIBUTING.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# name -> command. Order matters: cheap, fix-first checks run before slow ones.
CHECKS: dict[str, list[str]] = {
    "lint": ["ruff", "check", "."],
    "format": ["ruff", "format", "--check", "."],
    "typecheck": ["mypy"],
    "docs": [sys.executable, "scripts/validate_docs.py"],
    "alembic": [sys.executable, "scripts/validate_alembic.py"],
    "tests": ["pytest"],
}

FIXABLE = {
    "lint": ["ruff", "check", "--fix", "."],
    "format": ["ruff", "format", "."],
}


def run(name: str, command: list[str]) -> bool:
    print(f"\n=== {name}: {' '.join(command)} ===", flush=True)
    result = subprocess.run(command, cwd=REPO_ROOT)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "checks",
        nargs="*",
        choices=list(CHECKS),
        help="Subset of checks to run (default: all).",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix lint and formatting before checking.",
    )
    args = parser.parse_args()

    selected = args.checks or list(CHECKS)

    if args.fix:
        for name in ("lint", "format"):
            if name in selected:
                run(f"{name} (fix)", FIXABLE[name])

    failures = [name for name in selected if not run(name, CHECKS[name])]

    print("\n" + "=" * 48)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print(f"OK: {', '.join(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
