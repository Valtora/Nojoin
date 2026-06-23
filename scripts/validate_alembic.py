#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "backend" / "alembic" / "versions"
REVISION_RE = re.compile(r"revision\s*(?::[^=]+)?=\s*['\"]([0-9a-f]+)['\"]")
DOWN_REVISION_RE = re.compile(r"down_revision\s*(?::[^=]+)?=\s*(.+)")
REVISION_ID_RE = re.compile(r"['\"]([0-9a-f]+)['\"]")


def iter_revision_files() -> list[Path]:
    return sorted(path for path in VERSIONS_DIR.glob("*.py") if path.is_file())


def main() -> int:
    revision_files = iter_revision_files()
    if not revision_files:
        print("No Alembic revision files found.", file=sys.stderr)
        return 1

    graph: dict[str, tuple[str, ...]] = {}
    revision_counts: Counter[str] = Counter()

    for revision_file in revision_files:
        source = revision_file.read_text(encoding="utf-8")
        revision_match = REVISION_RE.search(source)
        if revision_match is None:
            print(
                f"Missing revision id in {revision_file.relative_to(REPO_ROOT)}.",
                file=sys.stderr,
            )
            return 1

        revision_id = revision_match.group(1)
        revision_counts[revision_id] += 1

        down_revision_match = DOWN_REVISION_RE.search(source)
        parent_ids: tuple[str, ...] = ()
        if down_revision_match is not None:
            parent_ids = tuple(REVISION_ID_RE.findall(down_revision_match.group(1)))

        graph[revision_id] = parent_ids

    duplicate_revisions = sorted(
        revision_id for revision_id, count in revision_counts.items() if count > 1
    )
    if duplicate_revisions:
        print(
            "Duplicate Alembic revision ids found: " + ", ".join(duplicate_revisions),
            file=sys.stderr,
        )
        return 1

    known_revisions = set(graph)
    missing_parents = sorted(
        {
            parent_id
            for parent_ids in graph.values()
            for parent_id in parent_ids
            if parent_id not in known_revisions
        }
    )
    if missing_parents:
        print(
            "Alembic graph references missing parent revision(s): "
            + ", ".join(missing_parents),
            file=sys.stderr,
        )
        return 1

    referenced_revisions = {
        parent_id for parent_ids in graph.values() for parent_id in parent_ids
    }
    heads = sorted(known_revisions - referenced_revisions)
    if not heads:
        print("Could not determine an Alembic head revision.", file=sys.stderr)
        return 1

    if len(heads) != 1:
        print(
            "Expected exactly one checked-in Alembic head, found: " + ", ".join(heads),
            file=sys.stderr,
        )
        return 1

    print(f"Alembic migration graph validated successfully. Head revision: {heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
