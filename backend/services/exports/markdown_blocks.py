"""Meeting notes, which are stored as Markdown, as export blocks.

Parsing uses ``markdown-it-py`` with ``MARKDOWN_RULES``: CommonMark plus the
two GFM rules the notes editor can produce. Every export format reads the same
parse, so the formats can never disagree about what counts as a table.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .model import (
    LINE_BREAK,
    Block,
    CodeBlock,
    Heading,
    Inline,
    ListBlock,
    ListItem,
    Paragraph,
    Rule,
    Span,
    Table,
)

# CommonMark plus the GFM table and strikethrough rules.
MARKDOWN_RULES = ("table", "strikethrough")

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_LINE_BREAK_PATTERN = re.compile(r"<br\s*/?>", re.IGNORECASE)

_MARK_TOGGLES: dict[str, tuple[str, bool]] = {
    "strong_open": ("bold", True),
    "strong_close": ("bold", False),
    "em_open": ("italic", True),
    "em_close": ("italic", False),
    "s_open": ("strike", True),
    "s_close": ("strike", False),
}


def build_parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable(list(MARKDOWN_RULES))


class _Marks:
    """Formatting marks open while walking one inline token stream."""

    def __init__(self) -> None:
        self.bold = False
        self.italic = False
        self.strike = False
        self.link: str | None = None

    def span(self, text: str, *, code: bool = False) -> Span:
        return Span(
            text,
            bold=self.bold,
            italic=self.italic,
            strike=self.strike,
            code=code,
            link=self.link,
        )


def _inline_html(content: str, marks: _Marks) -> list[Span]:
    """The inline HTML Markdown tables rely on.

    A line break inside a table cell has no Markdown spelling, so the editor and
    the notes prompt both write ``<br>``. Any other inline HTML is reduced to
    its text so markup never reaches the page.
    """
    if _LINE_BREAK_PATTERN.fullmatch(content.strip()):
        return [LINE_BREAK]
    text = _HTML_TAG_PATTERN.sub("", content)
    return [marks.span(text)] if text else []


def _inline_token_spans(token: Token, marks: _Marks) -> list[Span]:
    if token.type == "text":
        return [marks.span(token.content)] if token.content else []
    if token.type == "code_inline":
        return [marks.span(token.content, code=True)]
    if token.type in ("softbreak", "hardbreak"):
        return [LINE_BREAK]
    if token.type == "html_inline":
        return _inline_html(token.content, marks)
    if token.type == "image":
        return [marks.span(token.content)] if token.content else []
    return []


def inline_spans(children: Iterable[Token] | None) -> Inline:
    """An inline token stream as spans."""
    marks = _Marks()
    spans: list[Span] = []
    for token in children or []:
        toggle = _MARK_TOGGLES.get(token.type)
        if toggle is not None:
            setattr(marks, toggle[0], toggle[1])
        elif token.type == "link_open":
            href = token.attrGet("href")
            marks.link = str(href) if href else None
        elif token.type == "link_close":
            marks.link = None
        else:
            spans.extend(_inline_token_spans(token, marks))
    return tuple(spans)


def _collect_table(tokens: Sequence[Token], index: int) -> tuple[Table, int]:
    """A table's cells as spans, plus the index after the table."""
    header: list[Inline] = []
    rows: list[list[Inline]] = []
    in_header = False
    current: list[Inline] | None = None
    cursor = index + 1
    while cursor < len(tokens) and tokens[cursor].type != "table_close":
        token = tokens[cursor]
        if token.type in ("thead_open", "thead_close"):
            in_header = token.type == "thead_open"
        elif token.type == "tr_open":
            current = []
        elif token.type == "tr_close" and current is not None:
            if in_header:
                header = current
            else:
                rows.append(current)
            current = None
        elif token.type in ("th_open", "td_open") and current is not None:
            inline = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
            current.append(
                inline_spans(inline.children)
                if inline is not None and inline.type == "inline"
                else ()
            )
        cursor += 1

    # Ragged rows are padded rather than dropped, so a short row keeps its
    # cells in the columns they were written in.
    width = max([len(header), *(len(row) for row in rows)], default=0)
    padded = tuple(tuple(row) + ((),) * (width - len(row)) for row in rows)
    return Table(header=tuple(header) + ((),) * (width - len(header)), rows=padded), (
        cursor + 1
    )


class _NotesConverter:
    """Walks a flat markdown-it token stream and emits blocks."""

    def __init__(self, heading_offset: int) -> None:
        self._heading_offset = heading_offset
        self._blocks: list[Block] = []
        self._list_stack: list[bool] = []
        self._list_items: list[ListItem] = []
        self._item_open = False
        self._quote_depth = 0
        self._handlers: dict[str, Callable[[Sequence[Token], int], int]] = {
            "heading_open": self._heading,
            "paragraph_open": self._paragraph,
            "blockquote_open": self._quote_open,
            "blockquote_close": self._quote_close,
            "fence": self._code,
            "code_block": self._code,
            "hr": self._rule,
            "table_open": self._table,
            "bullet_list_open": self._list_open,
            "ordered_list_open": self._list_open,
            "bullet_list_close": self._list_close,
            "ordered_list_close": self._list_close,
            "list_item_open": self._item_start,
            "html_block": self._html_block,
        }

    def convert(self, tokens: Sequence[Token]) -> tuple[Block, ...]:
        index = 0
        while index < len(tokens):
            handler = self._handlers.get(tokens[index].type)
            index = handler(tokens, index) if handler else index + 1
        return tuple(self._blocks)

    def _emit(self, block: Block) -> None:
        # A table or code block inside a list item lands after the items
        # before it, not ahead of the whole list.
        self._flush_list()
        self._blocks.append(block)

    def _flush_list(self) -> None:
        if self._list_items:
            self._blocks.append(ListBlock(tuple(self._list_items)))
            self._list_items = []

    def _inline_at(self, tokens: Sequence[Token], index: int) -> Inline:
        if index < len(tokens) and tokens[index].type == "inline":
            return inline_spans(tokens[index].children)
        return ()

    def _heading(self, tokens: Sequence[Token], index: int) -> int:
        level = int(tokens[index].tag[1:]) + self._heading_offset
        self._emit(
            Heading(level=max(1, level), text=self._inline_at(tokens, index + 1))
        )
        return index + 2

    def _paragraph(self, tokens: Sequence[Token], index: int) -> int:
        spans = self._inline_at(tokens, index + 1)
        if self._list_stack:
            self._add_to_list(spans)
        elif spans:
            self._emit(Paragraph(spans, tone="quote" if self._quote_depth else "body"))
        return index + 2

    def _add_to_list(self, spans: Inline) -> None:
        # A second paragraph inside one list item continues that item rather
        # than becoming a bullet of its own.
        if not self._item_open and self._list_items:
            last = self._list_items[-1]
            self._list_items[-1] = ListItem(
                text=last.text + (LINE_BREAK,) + spans,
                depth=last.depth,
                ordered=last.ordered,
            )
            return
        self._list_items.append(
            ListItem(
                text=spans,
                depth=len(self._list_stack) - 1,
                ordered=self._list_stack[-1],
            )
        )
        self._item_open = False

    def _item_start(self, tokens: Sequence[Token], index: int) -> int:
        self._item_open = True
        return index + 1

    def _list_open(self, tokens: Sequence[Token], index: int) -> int:
        self._list_stack.append(tokens[index].type == "ordered_list_open")
        return index + 1

    def _list_close(self, tokens: Sequence[Token], index: int) -> int:
        if self._list_stack:
            self._list_stack.pop()
        if not self._list_stack:
            self._flush_list()
        return index + 1

    def _quote_open(self, tokens: Sequence[Token], index: int) -> int:
        self._quote_depth += 1
        return index + 1

    def _quote_close(self, tokens: Sequence[Token], index: int) -> int:
        self._quote_depth = max(0, self._quote_depth - 1)
        return index + 1

    def _code(self, tokens: Sequence[Token], index: int) -> int:
        self._emit(CodeBlock(tokens[index].content.rstrip("\n")))
        return index + 1

    def _rule(self, tokens: Sequence[Token], index: int) -> int:
        self._emit(Rule())
        return index + 1

    def _table(self, tokens: Sequence[Token], index: int) -> int:
        table, next_index = _collect_table(tokens, index)
        if table.column_count:
            self._emit(table)
        return next_index

    def _html_block(self, tokens: Sequence[Token], index: int) -> int:
        text = _LINE_BREAK_PATTERN.sub("\n", tokens[index].content)
        text = _HTML_TAG_PATTERN.sub("", text).strip()
        if text:
            self._emit(Paragraph((Span(text),)))
        return index + 1


def notes_to_blocks(markdown_text: str) -> tuple[Block, ...]:
    """Notes as blocks, with their headings shifted so the shallowest is level 1.

    Generated notes open at ``##`` while hand-written ones may open at ``#``.
    Either way the notes' own top heading sits directly beneath the section
    title the export adds.
    """
    tokens = build_parser().parse(markdown_text or "")
    levels = [int(token.tag[1:]) for token in tokens if token.type == "heading_open"]
    offset = 1 - min(levels) if levels else 0
    return _NotesConverter(offset).convert(tokens)


# -- plain text -------------------------------------------------------------


def _cell_sources(tokens: Sequence[Token], index: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    cursor = index + 1
    while cursor < len(tokens) and tokens[cursor].type != "table_close":
        token = tokens[cursor]
        if token.type == "tr_open":
            rows.append([])
        elif token.type == "inline" and rows:
            rows[-1].append(token.content.strip())
        cursor += 1
    return rows, cursor


def _column_alignments(tokens: Sequence[Token], index: int) -> list[str]:
    alignments: list[str] = []
    cursor = index + 1
    while cursor < len(tokens) and tokens[cursor].type != "thead_close":
        if tokens[cursor].type == "th_open":
            style = str(tokens[cursor].attrGet("style") or "")
            alignments.append(style.removeprefix("text-align:"))
        cursor += 1
    return alignments


def _aligned_table(rows: list[list[str]], alignments: list[str]) -> list[str]:
    width = max(len(row) for row in rows)
    cells = [row + [""] * (width - len(row)) for row in rows]
    widths = [max(3, *(len(row[column]) for row in cells)) for column in range(width)]
    alignments = alignments + [""] * (width - len(alignments))

    def line(row: list[str]) -> str:
        padded = [
            cell.rjust(widths[column])
            if alignments[column] == "right"
            else cell.ljust(widths[column])
            for column, cell in enumerate(row)
        ]
        return "| " + " | ".join(padded) + " |"

    def rule(column: int) -> str:
        dashes = "-" * widths[column]
        if alignments[column] == "right":
            return dashes[:-1] + ":"
        if alignments[column] == "center":
            return ":" + dashes[1:-1] + ":"
        return dashes

    separator = "| " + " | ".join(rule(column) for column in range(width)) + " |"
    return [line(cells[0]), separator, *(line(row) for row in cells[1:])]


def realign_markdown_tables(markdown_text: str) -> str:
    """The notes as written, with each top-level pipe table padded into columns.

    A plain-text export keeps notes as Markdown so they paste into anything
    that reads it, but an unaligned pipe table is hard to read as text. Padding
    the cells keeps the table valid Markdown and makes the columns line up.
    """
    if not markdown_text:
        return ""
    tokens = build_parser().parse(markdown_text)
    lines = markdown_text.split("\n")
    replacements: list[tuple[int, int, list[str]]] = []
    for index, token in enumerate(tokens):
        if token.type != "table_open" or token.level != 0 or token.map is None:
            continue
        rows, _ = _cell_sources(tokens, index)
        if rows:
            replacements.append(
                (
                    token.map[0],
                    token.map[1],
                    _aligned_table(rows, _column_alignments(tokens, index)),
                )
            )
    for start, end, replacement in reversed(replacements):
        lines[start:end] = replacement
    return "\n".join(lines)
