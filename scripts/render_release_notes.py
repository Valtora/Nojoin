#!/usr/bin/env python3
"""Render a categorised, human-readable changelog for a git revision range.

Used by the `publish-release-notes` job in `.github/workflows/release.yml` to fill
the `{{CHANGELOG}}` placeholder in `.github/release-notes-template.md`. Commits are
grouped by their Conventional Commit type into readable sections; noisy internal
churn (chore/refactor/test/ci/build/style and anything non-conventional) is folded
into a collapsed "Other changes" block so the headline sections stay scannable.

Usage:
    python3 scripts/render_release_notes.py <git-range>      # e.g. v1.3.8..v1.3.9
    python3 scripts/render_release_notes.py <single-ref>     # full history up to ref

Prints the changelog markdown to stdout. Never fails the release on an empty range:
it emits a "No notable changes." line instead.
"""

from __future__ import annotations

import re
import subprocess
import sys

# Section title -> the Conventional Commit types that map into it, in display order.
# Breaking Changes and Security are routed by heuristics below, not by type.
TYPE_SECTIONS: list[tuple[str, set[str]]] = [
    ("New Features", {"feat"}),
    ("Bug Fixes", {"fix"}),
    ("Performance", {"perf"}),
    ("Documentation", {"docs"}),
]
BREAKING = "Breaking Changes"
SECURITY = "Security"
OTHER = "Other changes"

_SUBJECT = re.compile(
    r"^(?P<type>\w+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<msg>.*)$"
)
# Security markers used across this repo's history: CVE/GHSA advisory ids and the
# internal SEC-NNN issue tags. The `\b` guards against matching inside words
# (e.g. "msec-5"). The `sec` and `security` commit scopes are handled separately.
_SECURITY_HINT = re.compile(r"\b(?:CVE-\d|GHSA-|SEC-\d)", re.IGNORECASE)
_SECURITY_SCOPES = {"security", "sec"}


def _commits(rng: str) -> list[tuple[str, str]]:
    """Return [(short_hash, subject)] for non-merge commits in the range."""
    result = subprocess.run(
        ["git", "log", "--no-merges", "--pretty=format:%h\x1f%s", rng],
        capture_output=True,
        text=True,
        check=True,
    )
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "\x1f" in line:
            short, subject = line.split("\x1f", 1)
            rows.append((short, subject))
    return rows


def _summarise(message: str) -> str:
    """Keep only the headline summary, dropping any ` — detail` tail.

    Conventional Commit subjects in this repo put a short summary before an
    em-dash and the rationale after it; the release notes only need the summary.
    """
    return message.split("—", 1)[0].strip()


def _format_entry(short: str, scope: str | None, message: str, raw_subject: str) -> str:
    body = _summarise(message) or raw_subject
    body = body[:1].upper() + body[1:] if body else raw_subject
    prefix = f"**{scope}:** " if scope else ""
    return f"- {prefix}{body} (`{short}`)"


def _parse(subject: str) -> tuple[str | None, str | None, bool, str]:
    """Return (type, scope, is_breaking, message) for a commit subject."""
    match = _SUBJECT.match(subject)
    if not match:
        return None, None, False, subject
    return (
        match.group("type").lower(),
        match.group("scope"),
        bool(match.group("bang")),
        match.group("msg"),
    )


def _section_for(typ: str | None, scope: str | None, bang: bool, subject: str) -> str:
    """Pick the changelog section a commit belongs in.

    Breaking changes and security fixes win over type; `maintenance`-scoped
    tracker churn is folded into the collapsed block.
    """
    if bang:
        return BREAKING
    if scope in _SECURITY_SCOPES or _SECURITY_HINT.search(subject):
        return SECURITY
    if scope == "maintenance":
        return OTHER
    for name, types in TYPE_SECTIONS:
        if typ in types:
            return name
    return OTHER


def render(rng: str) -> str:
    names = [BREAKING, SECURITY, *(n for n, _ in TYPE_SECTIONS), OTHER]
    buckets: dict[str, list[str]] = {name: [] for name in names}

    for short, subject in _commits(rng):
        typ, scope, bang, message = _parse(subject)
        entry = _format_entry(short, scope, message, subject)
        buckets[_section_for(typ, scope, bang, subject)].append(entry)

    parts: list[str] = []
    for name in [BREAKING, SECURITY] + [n for n, _ in TYPE_SECTIONS]:
        if buckets[name]:
            parts.append(f"#### {name}\n\n" + "\n".join(buckets[name]))
    if buckets[OTHER]:
        parts.append(
            f"<details>\n<summary>{OTHER} ({len(buckets[OTHER])})</summary>\n\n"
            + "\n".join(buckets[OTHER])
            + "\n</details>"
        )

    return "\n\n".join(parts) if parts else "- No notable changes."


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render_release_notes.py <git-range>", file=sys.stderr)
        return 2
    print(render(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
