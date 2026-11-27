"""scripts/cut_release_notes.py turns docs/releases/next.md into a version's page (#1017).

The maintainer runs it in the version-bump pull request. It writes the page
without guidance comments or empty sections, resets next.md to the skeleton
and lists the version first in the index, and it refuses a cut that would lose
or overwrite notes. The release host runs it with its system Python, so it uses
the standard library only.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from scripts.cut_release_notes import (
    CHANGE_AREAS,
    CutError,
    cut,
    cut_notes,
    format_date,
    next_heading_problems,
    next_headings,
    next_skeleton,
)
from scripts.validate_docs import release_pages_problems

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "cut_release_notes.py"
RELEASED = date(2026, 11, 19)


def _with_entries(entries: dict[str, str]) -> str:
    """The skeleton with each entry after its heading."""
    text = next_skeleton()
    for heading, entry in entries.items():
        assert text.count(f"{heading}\n") == 1, heading
        text = text.replace(f"{heading}\n", f"{heading}\n\n{entry}\n", 1)
    return text


ENTRIES = {
    "## Breaking changes": "- **Each user accepts the terms of use.** Alice Example accepts once (#1).",
    "### Recording and capture": "- An import shows the server's message (#2). <!-- reviewer: keep -->",
    "### API and MCP": "- `/users/me` carries a flag (#3).",
    "## Upgrade": "Run these:\n\n```bash\n\n\ndocker compose pull\n```",
    "## Known issues": "- Live lines can move words between speakers (#4).",
}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of docs/releases, with next.md holding the entries above."""
    shutil.copytree(REPO_ROOT / "docs" / "releases", tmp_path / "docs" / "releases")
    (tmp_path / "docs/releases/next.md").write_text(
        _with_entries(ENTRIES), encoding="utf-8"
    )
    return tmp_path


def test_the_skeleton_has_the_fixed_headings_in_order() -> None:
    assert next_heading_problems(next_skeleton()) == []
    assert next_headings()[0] == "# Next release"
    assert len(CHANGE_AREAS) <= 8


def test_the_page_keeps_entries_and_drops_comments_and_empty_sections() -> None:
    page = cut_notes(_with_entries(ENTRIES), "9.9.0", RELEASED)

    assert page == "\n".join(
        [
            "# Nojoin 9.9.0",
            "",
            "Released 19 November 2026. The image digests and the list of commits "
            "are on the [GitHub release](https://github.com/Valtora/Nojoin/releases/tag/v9.9.0).",
            "",
            "## Breaking changes",
            "",
            "- **Each user accepts the terms of use.** Alice Example accepts once (#1).",
            "",
            "## Changes",
            "",
            "### Recording and capture",
            "",
            "- An import shows the server's message (#2).",
            "",
            "### API and MCP",
            "",
            "- `/users/me` carries a flag (#3).",
            "",
            "## Upgrade",
            "",
            "Run these:",
            "",
            "```bash",
            "",
            "",
            "docker compose pull",
            "```",
            "",
            "## Known issues",
            "",
            "- Live lines can move words between speakers (#4).",
            "",
        ]
    )


def test_an_empty_next_page_cuts_to_the_title_alone() -> None:
    page = cut_notes(next_skeleton(), "9.9.0", RELEASED)

    assert page.startswith("# Nojoin 9.9.0\n\nReleased 19 November 2026.")
    assert "##" not in page


def test_dates_read_in_british_order() -> None:
    assert format_date(date(2026, 1, 2)) == "2 January 2026"


def test_a_cut_writes_the_page_resets_next_and_lists_the_version(tree: Path) -> None:
    page = cut(tree, "9.9.0", RELEASED)

    assert page == tree / "docs/releases/9.9.0.md"
    assert "Alice Example accepts once" in page.read_text(encoding="utf-8")
    assert (tree / "docs/releases/next.md").read_text(
        encoding="utf-8"
    ) == next_skeleton()
    index = (tree / "docs/releases/README.md").read_text(encoding="utf-8").splitlines()
    versions = [line for line in index if line.startswith("- [")]
    assert versions[:2] == [
        "- [9.9.0](9.9.0.md), 19 November 2026",
        "- [3.1.0](3.1.0.md), 11 November 2026",
    ]
    assert release_pages_problems("9.9.0", tree) == []


def test_the_first_version_goes_under_the_heading(tree: Path) -> None:
    index = tree / "docs/releases/README.md"
    index.write_text("# Release Notes\n\n## Versions\n\nOlder notes are on GitHub.\n")

    cut(tree, "9.9.0", RELEASED)

    assert index.read_text(encoding="utf-8") == (
        "# Release Notes\n\n## Versions\n\n- [9.9.0](9.9.0.md), 19 November 2026\n\n"
        "Older notes are on GitHub.\n"
    )


@pytest.mark.parametrize(
    ("version", "prepare", "message"),
    [
        ("9.9", None, "'9.9' is not a version of the form X.Y.Z."),
        ("3.1.0", None, "docs/releases/3.1.0.md already exists."),
        (
            "9.9.0",
            lambda root: (root / "docs/releases/next.md").unlink(),
            "docs/releases/next.md is missing",
        ),
        (
            "9.9.0",
            lambda root: (root / "docs/releases/README.md").write_text(
                "# Release Notes\n\n## Versions\n\n- [9.9.0](9.9.0.md), 1 May 2026\n"
            ),
            "docs/releases/README.md already lists 9.9.0.",
        ),
        (
            "9.9.0",
            lambda root: (root / "docs/releases/README.md").write_text(
                "# Release Notes\n"
            ),
            "has no `## Versions` heading",
        ),
        (
            "9.9.0",
            lambda root: (root / "docs/releases/next.md").write_text(
                _with_entries(ENTRIES).replace("## Rollback\n", "")
            ),
            "it has lost `## Rollback`",
        ),
    ],
)
def test_a_cut_that_would_lose_or_overwrite_notes_is_refused(
    tree: Path, version: str, prepare, message: str
) -> None:
    if prepare:
        prepare(tree)
    before = {path: path.read_bytes() for path in (tree / "docs/releases").iterdir()}

    with pytest.raises(CutError) as refused:
        cut(tree, version, RELEASED)

    assert message in str(refused.value)
    after = {path: path.read_bytes() for path in (tree / "docs/releases").iterdir()}
    assert after == before


def _script_copy(tree: Path) -> Path:
    (tree / "scripts").mkdir()
    shutil.copy(SCRIPT, tree / "scripts" / SCRIPT.name)
    return tree / "scripts" / SCRIPT.name


def test_the_script_cuts_the_tree_it_sits_in(tree: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_script_copy(tree)), "9.9.0", "--date", "2026-11-19"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote docs/releases/9.9.0.md from docs/releases/next.md." in result.stdout
    assert (tree / "docs/releases/9.9.0.md").is_file()
    assert not (REPO_ROOT / "docs/releases/9.9.0.md").exists()


def test_the_script_reports_a_refusal_and_exits_1(tree: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_script_copy(tree)), "3.1.0"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "docs/releases/3.1.0.md already exists." in result.stderr


def test_the_script_prints_the_skeleton() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--skeleton"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == next_skeleton()


def test_the_script_imports_the_standard_library_only() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        name.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for name in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
