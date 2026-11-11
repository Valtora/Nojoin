#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / ".github" / "CONTRIBUTING.md",
    *sorted((REPO_ROOT / "docs").rglob("*.md")),
]
# release.yml fills these when it publishes a release. A rewrite of the notes
# that drops one publishes the release without that part, as 2.10.0 shipped
# with no changelog (#480).
RELEASE_NOTES_TEMPLATE = REPO_ROOT / ".github" / "release-notes-template.md"
RELEASE_NOTES_PLACEHOLDERS = ("{{VERSION}}", "{{IMAGE_DIGESTS}}", "{{CHANGELOG}}")
# The terms of use the app shows come from LEGAL_TERMS; LEGAL.md is the page
# people read and link to. They must say the same, word for word (#1004).
LEGAL_DOC = REPO_ROOT / "docs" / "LEGAL.md"
LEGAL_TERMS = REPO_ROOT / "frontend" / "src" / "lib" / "legalTerms.json"
LEGAL_SECTION_RE = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
LINK_RE = re.compile(r"(!?\[[^\]]*])\(([^)]+)\)")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def normalize_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return target


def should_skip_target(target: str) -> bool:
    return (
        not target
        or target.startswith("#")
        or target.startswith("mailto:")
        or target.startswith("tel:")
        or target.startswith("data:")
        or target.startswith("javascript:")
        or target.startswith("//")
        or SCHEME_RE.match(target) is not None
    )


def resolve_local_target(source_file: Path, target: str) -> Path:
    path_text = target.split("#", 1)[0].split("?", 1)[0]
    if path_text.startswith("/"):
        return REPO_ROOT / path_text.lstrip("/")
    return (source_file.parent / path_text).resolve()


def missing_release_notes_placeholders(template: str) -> list[str]:
    return [
        placeholder
        for placeholder in RELEASE_NOTES_PLACEHOLDERS
        if placeholder not in template
    ]


def _markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    """Paragraphs and bullet lists, in order, shaped as legalTerms.json shapes them.

    A blockquote counts as a paragraph, and an HTML comment as nothing.
    """
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    blocks: list[dict[str, Any]] = []
    for chunk in re.split(r"\n\s*\n", markdown.strip()):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.startswith("- ") for line in lines):
            blocks.append({"list": [line[2:].strip() for line in lines]})
        else:
            text = " ".join(line.removeprefix(">").strip() for line in lines)
            blocks.append({"paragraph": text})
    return blocks


def legal_terms_drift(legal_markdown: str, terms: dict[str, Any]) -> list[str]:
    """Where LEGAL.md and the app's terms of use say different things.

    Each section of the terms must match the LEGAL.md section of the same
    number: the heading apart from case, since the docs write headings in title
    case and the app in sentence case, and every paragraph and bullet exactly.
    The closing sentence and the owner's sentence must each be a paragraph of
    LEGAL.md.
    """
    headings = list(LEGAL_SECTION_RE.finditer(legal_markdown))
    bodies = {
        int(match.group(1)): (
            match.group(2).strip(),
            legal_markdown[
                match.end() : headings[index + 1].start()
                if index + 1 < len(headings)
                else len(legal_markdown)
            ],
        )
        for index, match in enumerate(headings)
    }
    problems: list[str] = []
    for number, section in enumerate(terms["sections"], start=1):
        if number not in bodies:
            problems.append(f"LEGAL.md has no section {number}, {section['heading']}.")
            continue
        heading, body = bodies[number]
        if heading.casefold() != section["heading"].casefold():
            problems.append(
                f"Section {number} is headed {heading!r} in LEGAL.md and "
                f"{section['heading']!r} in the app."
            )
        if _markdown_blocks(body) != section["blocks"]:
            problems.append(
                f"Section {number}'s text differs between LEGAL.md and the app."
            )
    paragraphs = {
        block["paragraph"]
        for block in _markdown_blocks(legal_markdown)
        if "paragraph" in block
    }
    for key in ("closing", "ownerResponsibility"):
        if terms[key] not in paragraphs:
            problems.append(f"LEGAL.md does not have the app's {key} sentence.")
    return problems


def _print_problems(heading: str, entries: list[str]) -> None:
    if entries:
        print(heading, file=sys.stderr)
        for entry in entries:
            print(f"  - {entry}", file=sys.stderr)


def main() -> int:
    missing_placeholders = missing_release_notes_placeholders(
        RELEASE_NOTES_TEMPLATE.read_text(encoding="utf-8")
    )
    machine_local_links: list[str] = []
    missing_links: list[str] = []

    for markdown_file in MARKDOWN_FILES:
        content = markdown_file.read_text(encoding="utf-8")
        for line_number, line in enumerate(content.splitlines(), start=1):
            for _, raw_target in LINK_RE.findall(line):
                target = normalize_target(raw_target)
                if target.startswith("file:///"):
                    machine_local_links.append(
                        f"{markdown_file.relative_to(REPO_ROOT)}:{line_number}: {target}"
                    )
                    continue

                if should_skip_target(target):
                    continue

                resolved = resolve_local_target(markdown_file, target)
                if not resolved.exists():
                    missing_links.append(
                        f"{markdown_file.relative_to(REPO_ROOT)}:{line_number}: {target}"
                    )

    _print_problems(
        "Machine-local file:/// links are not allowed:", machine_local_links
    )
    _print_problems("Missing local documentation targets:", missing_links)

    if missing_placeholders:
        print(
            f"{RELEASE_NOTES_TEMPLATE.relative_to(REPO_ROOT)} has lost "
            f"{', '.join(missing_placeholders)}, which release.yml fills when it "
            "publishes a release. Put each back.",
            file=sys.stderr,
        )

    legal_drift = legal_terms_drift(
        LEGAL_DOC.read_text(encoding="utf-8"),
        json.loads(LEGAL_TERMS.read_text(encoding="utf-8")),
    )
    _print_problems(
        f"{LEGAL_DOC.relative_to(REPO_ROOT)} and "
        f"{LEGAL_TERMS.relative_to(REPO_ROOT)} must give the same terms of use:",
        legal_drift,
    )

    if machine_local_links or missing_links or missing_placeholders or legal_drift:
        return 1

    print(
        f"Documentation links validated successfully across {len(MARKDOWN_FILES)} Markdown files."
    )
    print("The release notes template keeps every placeholder release.yml fills.")
    print("LEGAL.md gives the terms of use the app shows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
