"""The neutral document an export is built as before any format renders it.

The TXT, PDF and DOCX exports used to be written three times over: plain text
inline in the route, PDF from a Markdown string, DOCX straight into
python-docx. Each carried its own copy of the header and section order, so a
change to one drifted from the others. Everything that decides *what* an export
says now produces these blocks once, and each renderer only decides how a block
looks in its format.

Blocks are frozen dataclasses so a renderer cannot mutate a document another
renderer is about to read, and so tests can compare them by value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

ParagraphTone = Literal["body", "muted", "small", "quote"]
CalloutTone = Literal["warning", "info", "neutral"]


@dataclass(frozen=True)
class Span:
    """A run of text with its inline marks.

    A span whose text is exactly ``"\\n"`` is a line break. ``color`` is a hex
    value for text that carries identity (a speaker's dot) or status (a tone
    label); renderers decide whether it can be used as a text colour as is.
    """

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    link: str | None = None
    color: str | None = None


LINE_BREAK = Span("\n")

Inline = tuple[Span, ...]


def plain(
    text: str, *, bold: bool = False, italic: bool = False, color: str | None = None
) -> Inline:
    """One span of text, or nothing for empty text."""
    return (Span(text, bold=bold, italic=italic, color=color),) if text else ()


def inline_text(spans: Inline) -> str:
    return "".join(span.text for span in spans)


@dataclass(frozen=True)
class Heading:
    """A heading inside a section. Level 1 sits directly under the section title."""

    level: int
    text: Inline


@dataclass(frozen=True)
class Paragraph:
    text: Inline
    tone: ParagraphTone = "body"


@dataclass(frozen=True)
class ListItem:
    text: Inline
    depth: int = 0
    ordered: bool = False


@dataclass(frozen=True)
class ListBlock:
    """A list flattened to items with a depth, the shape Word list styles take."""

    items: tuple[ListItem, ...]


@dataclass(frozen=True)
class Table:
    """A table with an optional header row.

    ``numeric`` names the columns that hold figures, which renderers right-align.
    ``row_colors`` carries a speaker colour per body row (or None), drawn as a
    marker beside the row so a person keeps their colour in every table.
    """

    header: tuple[Inline, ...]
    rows: tuple[tuple[Inline, ...], ...]
    numeric: frozenset[int] = frozenset()
    row_colors: tuple[str | None, ...] = ()

    @property
    def column_count(self) -> int:
        widths = [len(self.header), *(len(row) for row in self.rows)]
        return max(widths) if widths else 0


@dataclass(frozen=True)
class Rule:
    pass


@dataclass(frozen=True)
class CodeBlock:
    text: str


@dataclass(frozen=True)
class Stat:
    label: str
    value: str


@dataclass(frozen=True)
class StatRow:
    """The headline figures, drawn as tiles in PDF and DOCX."""

    stats: tuple[Stat, ...]


@dataclass(frozen=True)
class Callout:
    """A boxed note: a caveat on the figures, or a notice that a tier is absent."""

    tone: CalloutTone
    title: str | None
    body: tuple["Block", ...]


@dataclass(frozen=True)
class ChartBlock:
    """A chart for PDF and DOCX, with the same figures as blocks for plain text.

    ``chart`` is one of the specs in ``charts.py``. The fallback is not a
    degraded copy: plain text cannot draw, so it carries the figures the chart
    plots in a form that can be read without it.
    """

    chart: object
    fallback: tuple["Block", ...]


@dataclass(frozen=True)
class TranscriptLine:
    start_seconds: float
    speaker: str
    color: str | None
    text: str


Block = Union[
    Heading,
    Paragraph,
    ListBlock,
    Table,
    Rule,
    CodeBlock,
    StatRow,
    Callout,
    ChartBlock,
    TranscriptLine,
]


@dataclass(frozen=True)
class Section:
    """One top-level part of an export: notes, analytics or the transcript.

    ``markdown_source`` is set on the notes section only. Notes are stored as
    Markdown and a plain-text export keeps them as written, so the TXT renderer
    prints the source (with its tables realigned) instead of re-serialising
    blocks that were parsed from it.
    """

    key: str
    title: str
    blocks: tuple[Block, ...]
    badge: str | None = None
    markdown_source: str | None = None


@dataclass(frozen=True)
class MetaItem:
    label: str
    value: str


@dataclass(frozen=True)
class ExportDocument:
    title: str
    meta: tuple[MetaItem, ...]
    sections: tuple[Section, ...]
    # The date printed in the letterhead, already in the reader's timezone.
    dated: str
    # False drops the Nojoin logo, name and footer line; see docs/ADMIN.md.
    branded: bool = True
    speakers: tuple[str, ...] = field(default=())
