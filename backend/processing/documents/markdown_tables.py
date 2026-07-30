"""Rendering tabular data as Markdown, shared by every tabular format.

PDF tables, PPTX tables, Word tables, worksheets and CSV files all arrive as a
list of rows and all need the same escaping and the same header handling, so the
conversion lives in one place rather than four.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence


def _cell(value: object) -> str:
    """One cell, flattened and escaped so it cannot break the table."""
    if value is None:
        return ""
    text = str(value).strip()
    # A literal pipe would end the cell, and a newline would end the row.
    # Markdown offers no escape for the latter inside a table, so the line
    # break becomes a space -- the content survives, the structure survives.
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def rows_to_markdown_table(
    rows: Sequence[Sequence[object]],
    *,
    header: Optional[Sequence[object]] = None,
) -> str:
    """A Markdown table, or "" when there is nothing worth rendering.

    When no explicit header is given the first row becomes one. Markdown has no
    concept of a headerless table, and a table whose header row is really data
    still reads correctly -- whereas omitting the separator row produces
    something that renders as a paragraph of pipes.
    """
    cleaned: List[List[str]] = []
    for row in rows:
        cleaned.append([_cell(value) for value in row])

    if header is not None:
        header_cells = [_cell(value) for value in header]
    elif cleaned:
        header_cells = cleaned.pop(0)
    else:
        return ""

    if not header_cells or not any(header_cells) and not cleaned:
        return ""

    width = max([len(header_cells), *(len(row) for row in cleaned)] or [0])
    if width == 0:
        return ""

    def _pad(row: List[str]) -> List[str]:
        return row + [""] * (width - len(row))

    lines = [
        "| " + " | ".join(_pad(header_cells)) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(_pad(row)) + " |" for row in cleaned)
    return "\n".join(lines)


def iter_non_empty(values: Iterable[Optional[str]]) -> List[str]:
    """Strip and drop blanks, used when assembling a page from many fragments."""
    return [value.strip() for value in values if value and value.strip()]
