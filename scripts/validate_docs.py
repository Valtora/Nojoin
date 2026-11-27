#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# Run as a file, Python puts scripts/ on sys.path rather than the repository
# root, and the tests import this module as scripts.validate_docs. With the root
# on the path, the import below works both ways.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cut_release_notes import (  # noqa: E402
    COMMENT_RE,
    NEXT_NOTES,
    RELEASES_DIR,
    RELEASES_INDEX,
    index_lists,
    next_heading_problems,
)

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
# The body links the full notes at the release's own tag. It is absolute
# because Settings > Updates in the app shows the body too.
RELEASE_NOTES_FULL_LINK = (
    "https://github.com/Valtora/Nojoin/blob/v{{VERSION}}/docs/releases/{{VERSION}}.md"
)
# Limits on the hand-written part of the release body, which keeps to what an
# operator must do before upgrading and the main changes; the rest goes in
# docs/releases/<version>.md. A release with unusually many breaking changes
# raises them deliberately, in its version-bump pull request, rather than
# cutting a step an operator needs.
RELEASE_BODY_MAX_WORDS = 350
WHATS_NEW_HEADING = "### What's new"
WHATS_NEW_MAX_BULLETS = 5
WHATS_NEW_MAX_BULLET_WORDS = 40
DOCS_VERSION = REPO_ROOT / "docs" / "VERSION"
DETAILS_TAG_RE = re.compile(r"</?details\b[^>]*>", re.IGNORECASE)
FENCED_CODE_RE = re.compile(
    r"^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[^\n]*$", re.DOTALL | re.MULTILINE
)
BULLET_RE = re.compile(r"^[-*+] ")
WORD_RE = re.compile(r"[^\W_]")
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
    """The placeholders gone from the template, a mention in a comment aside."""
    template = COMMENT_RE.sub("", template)
    return [
        placeholder
        for placeholder in RELEASE_NOTES_PLACEHOLDERS
        if placeholder not in template
    ]


def _without_collapsed_blocks(text: str) -> str:
    """The text with each outermost <details> block, and all it holds, removed."""
    kept: list[str] = []
    depth = 0
    start = 0
    for tag in DETAILS_TAG_RE.finditer(text):
        if not tag.group(0).startswith("</"):
            if depth == 0:
                kept.append(text[start : tag.start()])
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                start = tag.end()
    if depth == 0:
        kept.append(text[start:])
    return "".join(kept)


def release_body_hand_written(template: str) -> str:
    """The part of the release body a maintainer writes and the limits cover.

    HTML comments, the placeholders, fenced code blocks and the collapsed
    <details> blocks are left out.
    """
    text = _without_collapsed_blocks(COMMENT_RE.sub("", template))
    text = FENCED_CODE_RE.sub("", text)
    for placeholder in RELEASE_NOTES_PLACEHOLDERS:
        text = text.replace(placeholder, "")
    return text


def count_words(text: str) -> int:
    """Runs of non-space characters with a letter or digit in them.

    List markers, heading marks and lone punctuation are not words. A link
    counts by the words of its text, and its address as one.
    """
    return sum(1 for token in text.split() if WORD_RE.search(token))


def whats_new_bullets(hand_written: str) -> list[str]:
    """Each top-level bullet under What's new, continuation lines included."""
    bullets: list[list[str]] = []
    current: list[str] | None = None
    in_section = False
    previous_blank = True
    for line in hand_written.splitlines():
        if line.startswith("#"):
            in_section = line.strip() == WHATS_NEW_HEADING
            current = None
        elif in_section and BULLET_RE.match(line):
            current = [line]
            bullets.append(current)
        elif current is not None and line.strip():
            if line[0].isspace() or not previous_blank:
                current.append(line)
            else:
                current = None
        previous_blank = not line.strip()
    return [" ".join(bullet) for bullet in bullets]


def release_body_problems(template: str) -> list[str]:
    """What keeps the release body template from being published as it is."""
    problems = [
        f"It has lost {placeholder}, which release.yml fills when it publishes "
        "a release. Put it back."
        for placeholder in missing_release_notes_placeholders(template)
    ]
    if RELEASE_NOTES_FULL_LINK not in COMMENT_RE.sub("", template):
        problems.append(
            f"It has lost the link to the full notes, {RELEASE_NOTES_FULL_LINK}. "
            "Put it back: readers on GitHub and in Settings > Updates follow it "
            "to everything the body leaves out."
        )
    hand_written = release_body_hand_written(template)
    words = count_words(hand_written)
    if words > RELEASE_BODY_MAX_WORDS:
        problems.append(
            f"Its hand-written part has {words} words, over the limit of "
            f"{RELEASE_BODY_MAX_WORDS}. Keep to what an operator must do before "
            "upgrading and the main changes, and leave the rest to "
            "docs/releases/<version>.md. Comments, placeholders, code blocks and "
            "the collapsed <details> blocks are not counted."
        )
    bullets = whats_new_bullets(hand_written)
    if len(bullets) > WHATS_NEW_MAX_BULLETS:
        problems.append(
            f"What's new has {len(bullets)} bullets. Keep the "
            f"{WHATS_NEW_MAX_BULLETS} that matter most and leave the rest to the "
            "full notes."
        )
    for number, bullet in enumerate(bullets, start=1):
        bullet_words = count_words(bullet)
        if bullet_words > WHATS_NEW_MAX_BULLET_WORDS:
            opening = " ".join(bullet.split()[1:7])
            problems.append(
                f"What's new bullet {number} ({opening} ...) has {bullet_words} "
                f"words, over {WHATS_NEW_MAX_BULLET_WORDS}. Make it one sentence "
                "and leave the detail to the full notes."
            )
    return problems


def release_pages_problems(version: str, root: Path = REPO_ROOT) -> list[str]:
    """What is missing from docs/releases for the version in docs/VERSION."""
    page = RELEASES_DIR / f"{version}.md"
    problems: list[str] = []
    if not (root / page).is_file():
        problems.append(
            f"docs/VERSION reads {version}, and {page} does not exist. Run "
            f"`python3 scripts/cut_release_notes.py {version}` in the "
            "version-bump pull request."
        )
    index = root / RELEASES_INDEX
    if not index.is_file() or not index_lists(
        index.read_text(encoding="utf-8"), version
    ):
        problems.append(
            f"{RELEASES_INDEX} does not list {version}. Add "
            f"`- [{version}]({version}.md), <release date>` at the top of its "
            "list, as scripts/cut_release_notes.py does."
        )
    next_notes = root / NEXT_NOTES
    if not next_notes.is_file():
        problems.append(
            f"{NEXT_NOTES} is missing. Restore it with "
            f"`python3 scripts/cut_release_notes.py --skeleton > {NEXT_NOTES}`."
        )
    else:
        problems += [
            f"{NEXT_NOTES} must keep the skeleton's headings, in order: {problem}. "
            "scripts/cut_release_notes.py defines them. Put entries under them as "
            "bullets, and leave a heading empty rather than removing it."
            for problem in next_heading_problems(next_notes.read_text(encoding="utf-8"))
        ]
    return problems


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


def link_problems() -> tuple[list[str], list[str]]:
    """Machine-local links, and local links whose target does not exist."""
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
    return machine_local_links, missing_links


def main() -> int:
    machine_local_links, missing_links = link_problems()
    template = RELEASE_NOTES_TEMPLATE.read_text(encoding="utf-8")
    version = DOCS_VERSION.read_text(encoding="utf-8").strip()
    groups = [
        ("Machine-local file:/// links are not allowed:", machine_local_links),
        ("Missing local documentation targets:", missing_links),
        (
            f"{RELEASE_NOTES_TEMPLATE.relative_to(REPO_ROOT)}, the GitHub release "
            "body, is not ready to publish:",
            release_body_problems(template),
        ),
        (
            f"{RELEASES_DIR} is missing the notes for {version}:",
            release_pages_problems(version),
        ),
        (
            f"{LEGAL_DOC.relative_to(REPO_ROOT)} and "
            f"{LEGAL_TERMS.relative_to(REPO_ROOT)} must give the same terms of use:",
            legal_terms_drift(
                LEGAL_DOC.read_text(encoding="utf-8"),
                json.loads(LEGAL_TERMS.read_text(encoding="utf-8")),
            ),
        ),
    ]
    for heading, entries in groups:
        _print_problems(heading, entries)
    if any(entries for _, entries in groups):
        return 1

    print(
        f"Documentation links validated successfully across {len(MARKDOWN_FILES)} Markdown files."
    )
    print(
        "The release body template keeps its placeholders and the full-notes link, "
        f"and its hand-written part is {count_words(release_body_hand_written(template))} "
        f"words of {RELEASE_BODY_MAX_WORDS}."
    )
    print(
        f"{RELEASES_DIR} has the page for {version} in its index, and next.md keeps "
        "its headings."
    )
    print("LEGAL.md gives the terms of use the app shows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
