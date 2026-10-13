#!/usr/bin/env python3
"""Fail on a blind-except suppression that does not say why.

docs/DEVELOPMENT.md asks every deliberate ``except Exception`` to carry
``# noqa: BLE001 -- boundary: <reason>``. The code is the suppression; the rest
is the justification a reviewer reads. Ruff only checks that the code is there,
so this script checks the shape: a bare ``# noqa: BLE001``, a different
separator, a missing ``boundary:`` tag, a placeholder or a one-word reason all
fail, and so does a blanket ``# noqa`` on an except line, which hides BLE001
along with everything else.

A handler with no reason worth writing should catch a narrower exception
instead of being given one.

    python scripts/check_blind_excepts.py
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCANNED_DIRS = ("backend", "scripts")

# The directive has to sit on the except line, which is where Ruff reads it.
EXCEPT_LINE = re.compile(r"^\s*except\b")
# Any noqa that names BLE001, however it is shaped.
MENTIONS_BLE001 = re.compile(r"#\s*noqa\b[^#]*\bBLE001\b")
# A noqa with no code list at all.
BLANKET_NOQA = re.compile(r"#\s*noqa(?!\s*:)")
# The required shape. Other codes may share the directive; a trailing
# comment such as a coverage pragma may follow the reason.
WELL_FORMED = re.compile(
    r"#\s*noqa:\s*(?:[A-Z]+\d+\s*,\s*)*BLE001(?:\s*,\s*[A-Z]+\d+)*"
    r"\s+--\s+boundary:\s+(?P<reason>\S.*)$"
)
PLACEHOLDERS = {"<reason>", "reason", "todo", "fixme", "xxx"}

HOW_TO = "write `# noqa: BLE001 -- boundary: <reason>` with a real reason, or catch a narrower exception"


def iter_source_files():
    for directory in SCANNED_DIRS:
        yield from sorted((REPO_ROOT / directory).rglob("*.py"))


def check_line(line: str) -> str | None:
    """Return a complaint for a malformed suppression, else None."""
    if not EXCEPT_LINE.match(line):
        return None
    if BLANKET_NOQA.search(line):
        return f"a blanket `# noqa` hides BLE001 without a reason; {HOW_TO}"
    if not MENTIONS_BLE001.search(line):
        return None
    match = WELL_FORMED.search(line)
    if match is None:
        return HOW_TO
    reason = match.group("reason").split("  #", 1)[0].strip()
    if reason.lower() in PLACEHOLDERS or len(reason.split()) < 2:
        return f"the reason has to say why this boundary catches everything; {HOW_TO}"
    return None


def main() -> int:
    failures: list[str] = []
    for path in iter_source_files():
        relative = path.relative_to(REPO_ROOT)
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            complaint = check_line(line)
            if complaint is not None:
                failures.append(f"{relative}:{number}: {complaint}")

    if failures:
        print("Blind-except suppressions without a justification:\n")
        print("\n".join(failures))
        print(f"\n{len(failures)} suppression(s) need a reason.")
        return 1

    print("Every BLE001 suppression carries a boundary reason.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
