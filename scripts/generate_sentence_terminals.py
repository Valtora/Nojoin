"""Regenerate the Unicode tables in ``backend/processing/sentence_start.py``.

``SENTENCE_TERMINALS`` is every code point matching ``\\p{Sentence_Terminal}``;
``_CLOSERS`` every code point matching ``[\\s\\p{Pe}\\p{Pf}\\p{Quotation_Mark}]``
(whitespace, closing brackets, final and other quotation marks), skipped before
a terminal. They are frozen in the module rather than built from
``unicodedata``, because the Python build's Unicode version lags (3.12 ships
15.0) and its ``str.isspace`` disagrees with ``\\s`` on U+001C-U+001F.

The tables come from the ``regex`` package, which carries its own Unicode data
(its release notes name the version; the script prints the package version).
Nojoin does not use it at run time. Transformers depends on it, and
``requirements/test.txt`` pins it for the check below::

    python scripts/generate_sentence_terminals.py          # print the tables
    python scripts/generate_sentence_terminals.py --check  # compare with the module

Paste the printed tables over the ones in the module, and update the Unicode
and regex versions in the comment above them. The backend suite makes the same
comparison (``backend/tests/test_sentence_start.py``), so ``scripts/check.py``
and CI's Backend tests fail on a table edited by hand. Because regex is pinned,
newer Unicode data shows on the Dependabot pull request that bumps it, and the
tables are regenerated there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

LINE_WIDTH = 100
TERMINAL_PATTERN = r"\p{Sentence_Terminal}"
CLOSER_PATTERN = r"[\s\p{Pe}\p{Pf}\p{Quotation_Mark}]"


def code_points(pattern: str) -> list[int]:
    import regex

    compiled = regex.compile(pattern)
    return [point for point in range(0x110000) if compiled.match(chr(point))]


def emit(name: str, points: list[int], *, as_set: bool) -> str:
    """The table as implicitly joined string literals of escapes."""
    escapes = [
        f"\\u{point:04x}" if point <= 0xFFFF else f"\\U{point:08x}" for point in points
    ]
    lines, line = [], ""
    for escape in escapes:
        if len(line) + len(escape) > LINE_WIDTH:
            lines.append(line)
            line = ""
        line += escape
    lines.append(line)
    if as_set:
        body = "\n".join(f'        "{text}"' for text in lines)
        return f"{name} = frozenset(\n    (\n{body}\n    )\n)\n"
    body = "\n".join(f'    "{text}"' for text in lines)
    return f"{name} = (\n{body}\n)\n"


def _differences(name: str, table: str, points: list[int]) -> list[str]:
    """How one table differs from its generated code points; empty if not at all."""
    generated = "".join(chr(point) for point in points)
    if table == generated:
        return []
    missing = sorted(set(generated) - set(table))
    extra = sorted(set(table) - set(generated))
    if not missing and not extra:
        return [f"{name}: the same code points in another order"]
    listed = [
        f"{what} " + " ".join(f"U+{ord(char):04X}" for char in chars)
        for what, chars in (("missing", missing), ("extra", extra))
        if chars
    ]
    return [f"{name}: {'; '.join(listed)}"]


def table_drift() -> list[str]:
    """How the module's tables differ from the generated ones; empty when they match."""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from backend.processing import sentence_start

    return _differences(
        "SENTENCE_TERMINALS",
        "".join(sorted(sentence_start.SENTENCE_TERMINALS)),
        code_points(TERMINAL_PATTERN),
    ) + _differences("_CLOSERS", sentence_start._CLOSERS, code_points(CLOSER_PATTERN))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the module's tables differ from the generated ones",
    )
    args = parser.parse_args()
    import regex

    print(f"# regex {regex.__version__}", file=sys.stderr)
    if not args.check:
        print(
            emit("SENTENCE_TERMINALS", code_points(TERMINAL_PATTERN), as_set=True),
            end="",
        )
        print(emit("_CLOSERS", code_points(CLOSER_PATTERN), as_set=False), end="")
        return 0
    drift = table_drift()
    for line in drift:
        print(line, file=sys.stderr)
    print("tables differ" if drift else "tables match", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
