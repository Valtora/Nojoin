"""Regenerate the Unicode tables in ``backend/processing/sentence_start.py``.

``SENTENCE_TERMINALS`` is every code point matching ``\\p{Sentence_Terminal}``;
``_CLOSERS`` every code point matching ``[\\s\\p{Pe}\\p{Pf}\\p{Quotation_Mark}]``
(whitespace, closing brackets, final and other quotation marks), skipped before
a terminal. They are frozen in the module rather than built from
``unicodedata``, because the Python build's Unicode version lags (3.12 ships
15.0) and its ``str.isspace`` disagrees with ``\\s`` on U+001C-U+001F.

The tables come from the ``regex`` package, which is not a Nojoin dependency
and carries its own Unicode data (its release notes name the version; the
script prints the package version)::

    pip install regex
    python scripts/generate_sentence_terminals.py          # print the tables
    python scripts/generate_sentence_terminals.py --check  # compare with the module

Paste the printed tables over the ones in the module, and update the Unicode
and regex versions in the comment above them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

LINE_WIDTH = 100


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the module's tables differ from the generated ones",
    )
    args = parser.parse_args()
    import regex

    terminals = code_points(r"\p{Sentence_Terminal}")
    closers = code_points(r"[\s\p{Pe}\p{Pf}\p{Quotation_Mark}]")
    print(f"# regex {regex.__version__}", file=sys.stderr)
    if not args.check:
        print(emit("SENTENCE_TERMINALS", terminals, as_set=True), end="")
        print(emit("_CLOSERS", closers, as_set=False), end="")
        return 0
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.processing import sentence_start

    same = sentence_start.SENTENCE_TERMINALS == {chr(p) for p in terminals} and (
        sentence_start._CLOSERS == "".join(chr(p) for p in closers)
    )
    print("tables match" if same else "tables differ", file=sys.stderr)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
