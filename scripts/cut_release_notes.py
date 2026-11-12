#!/usr/bin/env python3
"""Turn docs/releases/next.md into the notes page for a version.

Run it in the version-bump pull request:

    python3 scripts/cut_release_notes.py 3.2.0
    python3 scripts/cut_release_notes.py 3.2.0 --date 2026-11-19

It writes docs/releases/<version>.md from next.md, titled with the version and
dated (today unless --date says otherwise), without the guidance comments or
any section left empty. It then gives next.md a fresh skeleton and lists the
version at the top of docs/releases/README.md. It changes nothing else,
docs/VERSION included, and refuses a version that already has a page.

    python3 scripts/cut_release_notes.py --skeleton > docs/releases/next.md

prints the skeleton, to restore next.md by hand.

The skeleton and its fixed headings are defined here and nowhere else:
scripts/validate_docs.py checks next.md against them on every pull request.
Standard library only, so the release host's system Python runs it.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES_DIR = Path("docs") / "releases"
NEXT_NOTES = RELEASES_DIR / "next.md"
RELEASES_INDEX = RELEASES_DIR / "README.md"
RELEASE_URL = "https://github.com/Valtora/Nojoin/releases/tag/v{version}"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
INDEX_HEADING = "## Versions"
INDEX_LINE_RE = re.compile(r"^- \[(?P<version>\d+\.\d+\.\d+)\]\((?P=version)\.md\)")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6}) \S")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

NEXT_TITLE = "# Next release"

ENTRY_GUIDANCE = """\
Notes for the next release. A pull request that changes what a user, an
operator or an API client would notice adds its line here, under the heading
that fits, as a bullet:

- **Test connection** with no model selected asks you to choose one (#1009).

One or two sentences on what they would notice, with UI labels in bold as the
guides write them and the issue or pull request number in brackets at the end.
British English, no em dashes, no emojis, and no real people, companies or
meeting titles: use made-up names such as Alice Example. A dependency bump gets
a line only when an operator would notice it.

Keep every heading, empty or not. Cutting the release drops the empty ones
(scripts/cut_release_notes.py), and Docs validation fails when one is missing.
.github/CONTRIBUTING.md, "Release Notes", has the rule."""


@dataclass(frozen=True)
class Section:
    heading: str
    guidance: str
    areas: tuple[str, ...] = ()


# The areas split Changes so that pull requests merging side by side append in
# different places. Name them as the guides name these parts of Nojoin.
CHANGE_AREAS = (
    "Recording and capture",
    "Transcription and speakers",
    "Meetings, tags and search",
    "Notes and AI providers",
    "People, tasks and calendar",
    "Settings and administration",
    "API and MCP",
    "Deployment and dependencies",
)

SECTIONS = (
    Section(
        "Breaking changes",
        "What stops working or behaves differently after the upgrade, and what "
        "to do about it. Each also goes under Before you upgrade in the release "
        "body.",
    ),
    Section(
        "Changes",
        "One line per change, under the area it touches.",
        CHANGE_AREAS,
    ),
    Section(
        "Upgrade",
        "Steps beyond the usual upgrade commands, which the release body and "
        "DEPLOYMENT.md give.",
    ),
    Section(
        "Migration",
        "Each Alembic revision by id and what it adds or changes, new or "
        "retired config keys and environment variables, and the backup format.",
    ),
    Section(
        "Rollback",
        "The revision a downgrade goes back to, the order to roll back in, and "
        "what a downgrade loses.",
    ),
    Section(
        "Known issues",
        "Only problems Nojoin can fix, each with its open issue. A limit set by "
        "a dependency or a provider belongs in the guide for that feature.",
    ),
    Section(
        "Browser capture",
        "Changes to the supported browsers and operating systems, or to how "
        "capture behaves.",
    ),
)


class CutError(Exception):
    """A reason not to cut, worded for the maintainer running the script."""


def next_headings() -> list[str]:
    """The headings next.md keeps, in order."""
    headings = [NEXT_TITLE]
    for section in SECTIONS:
        headings.append(f"## {section.heading}")
        headings.extend(f"### {area}" for area in section.areas)
    return headings


def next_skeleton() -> str:
    """next.md as it starts each release: headings and guidance, no entries."""
    parts = [NEXT_TITLE, f"<!--\n{ENTRY_GUIDANCE}\n-->"]
    for section in SECTIONS:
        parts.append(f"## {section.heading}")
        parts.append(f"<!-- {section.guidance} -->")
        parts.extend(f"### {area}" for area in section.areas)
    return "\n\n".join(parts) + "\n"


def format_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def _markdown_lines(text: str) -> list[tuple[str, bool]]:
    """Each line with whether it sits in a fenced code block, comments removed."""
    lines: list[tuple[str, bool]] = []
    in_fence = False
    for line in COMMENT_RE.sub("", text.replace("\r\n", "\n")).split("\n"):
        if FENCE_RE.match(line):
            lines.append((line, True))
            in_fence = not in_fence
            continue
        lines.append((line, in_fence))
    return lines


def headings_in(text: str) -> list[str]:
    """Every heading outside comments and code blocks, in order."""
    return [
        line.rstrip()
        for line, in_fence in _markdown_lines(text)
        if not in_fence and HEADING_RE.match(line)
    ]


def next_heading_problems(text: str) -> list[str]:
    """How next.md's headings differ from the skeleton's, if they do."""
    expected = next_headings()
    found = headings_in(text)
    problems = [f"it has lost `{h}`" for h in expected if h not in found]
    problems += [
        f"`{h}` is not one of its headings" for h in found if h not in expected
    ]
    problems += [
        f"`{h}` appears {found.count(h)} times" for h in expected if found.count(h) > 1
    ]
    if not problems and found != expected:
        problems.append("its headings are out of order")
    return problems


def index_lists(index_text: str, version: str) -> bool:
    return any(
        (match := INDEX_LINE_RE.match(line)) and match.group("version") == version
        for line in index_text.splitlines()
    )


def _tidy(lines: list[tuple[str, bool]]) -> str:
    """Join lines with trailing spaces and repeated blank lines removed."""
    kept: list[str] = []
    for line, in_fence in lines:
        line = line.rstrip()
        if not in_fence and not line and kept and not kept[-1]:
            continue
        kept.append(line)
    return "\n".join(kept).strip("\n")


def _blocks(text: str) -> list[tuple[int, str, str]]:
    """(heading level, heading, body) for each heading, after the title's own."""
    blocks: list[tuple[int, str, list[tuple[str, bool]]]] = [(1, "", [])]
    for line, in_fence in _markdown_lines(text):
        match = None if in_fence else HEADING_RE.match(line)
        if match and len(match.group(1)) <= 3:
            blocks.append((len(match.group(1)), line.rstrip(), []))
        else:
            blocks[-1][2].append((line, in_fence))
    return [(level, heading, _tidy(body)) for level, heading, body in blocks]


def _kept_sections(blocks: list[tuple[int, str, str]]) -> list[str]:
    """The sections with something in them, each subsection kept the same way."""
    parts: list[str] = []
    section: list[str] = []
    section_has_content = False

    def close_section() -> None:
        if section and section_has_content:
            parts.extend(section)

    for level, heading, body in blocks:
        if level == 2:
            close_section()
            section = [heading] + ([body] if body else [])
            section_has_content = bool(body)
        elif level == 3 and body:
            section.extend([heading, body])
            section_has_content = True
        elif level == 1 and body:
            parts.append(body)
    close_section()
    return parts


def cut_notes(next_text: str, version: str, released: date) -> str:
    """The page for a version, made from next.md."""
    blocks = _blocks(next_text)
    header = [
        f"# Nojoin {version}",
        f"Released {format_date(released)}. The image digests and the list of "
        f"commits are on the [GitHub release]({RELEASE_URL.format(version=version)}).",
    ]
    return "\n\n".join(header + _kept_sections(blocks)) + "\n"


def add_to_index(index_text: str, version: str, released: date) -> str:
    """The index with the version first in its list."""
    entry = f"- [{version}]({version}.md), {format_date(released)}"
    lines = index_text.rstrip("\n").split("\n")
    for number, line in enumerate(lines):
        if INDEX_LINE_RE.match(line):
            lines.insert(number, entry)
            return "\n".join(lines) + "\n"
    if INDEX_HEADING not in lines:
        raise CutError(
            f"{RELEASES_INDEX} has no `{INDEX_HEADING}` heading to list the "
            "version under. Put it back above the list of versions."
        )
    after_heading = lines.index(INDEX_HEADING) + 1
    lines[after_heading:after_heading] = ["", entry]
    return "\n".join(lines) + "\n"


def _check_can_cut(root: Path, version: str) -> None:
    if not VERSION_RE.match(version):
        raise CutError(f"{version!r} is not a version of the form X.Y.Z.")
    page = RELEASES_DIR / f"{version}.md"
    if (root / page).exists():
        raise CutError(f"{page} already exists. A version is cut once.")
    if not (root / NEXT_NOTES).exists():
        raise CutError(f"{NEXT_NOTES} is missing, so there is nothing to cut.")
    if index_lists((root / RELEASES_INDEX).read_text(encoding="utf-8"), version):
        raise CutError(f"{RELEASES_INDEX} already lists {version}.")
    problems = next_heading_problems((root / NEXT_NOTES).read_text(encoding="utf-8"))
    if problems:
        raise CutError(
            f"{NEXT_NOTES} must keep the skeleton's headings before it is cut: "
            + "; ".join(problems)
            + ". Put them back, then run this again."
        )


def cut(root: Path, version: str, released: date) -> Path:
    """Write the version's page, reset next.md and list the version."""
    _check_can_cut(root, version)
    page = root / RELEASES_DIR / f"{version}.md"
    index = root / RELEASES_INDEX
    next_notes = root / NEXT_NOTES
    updated_index = add_to_index(index.read_text(encoding="utf-8"), version, released)
    page.write_text(
        cut_notes(next_notes.read_text(encoding="utf-8"), version, released),
        encoding="utf-8",
    )
    next_notes.write_text(next_skeleton(), encoding="utf-8")
    index.write_text(updated_index, encoding="utf-8")
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn docs/releases/next.md into the notes page for a version."
    )
    parser.add_argument("version", nargs="?", help="the version to cut, as X.Y.Z")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="the release date as YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--skeleton",
        action="store_true",
        help="print next.md's empty skeleton and change nothing",
    )
    args = parser.parse_args(argv)

    if args.skeleton:
        print(next_skeleton(), end="")
        return 0
    if not args.version:
        parser.error("give the version to cut, or --skeleton")

    try:
        page = cut(REPO_ROOT, args.version, args.date or date.today())
    except CutError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"Wrote {page.relative_to(REPO_ROOT)} from {NEXT_NOTES}.")
    print(
        f"Reset {NEXT_NOTES} to the skeleton and listed {args.version} in {RELEASES_INDEX}."
    )
    print(
        "Now write the release body in .github/release-notes-template.md from the "
        f"new page, and check docs/VERSION reads {args.version}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
