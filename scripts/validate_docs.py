#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from pathlib import Path

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

    if machine_local_links:
        print("Machine-local file:/// links are not allowed:", file=sys.stderr)
        for entry in machine_local_links:
            print(f"  - {entry}", file=sys.stderr)

    if missing_links:
        print("Missing local documentation targets:", file=sys.stderr)
        for entry in missing_links:
            print(f"  - {entry}", file=sys.stderr)

    if missing_placeholders:
        print(
            f"{RELEASE_NOTES_TEMPLATE.relative_to(REPO_ROOT)} has lost "
            f"{', '.join(missing_placeholders)}, which release.yml fills when it "
            "publishes a release. Put each back.",
            file=sys.stderr,
        )

    if machine_local_links or missing_links or missing_placeholders:
        return 1

    print(
        f"Documentation links validated successfully across {len(MARKDOWN_FILES)} Markdown files."
    )
    print("The release notes template keeps every placeholder release.yml fills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
